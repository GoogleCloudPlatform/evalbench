"""Orchestrator for query conversion with agentic loop."""

import json
import csv
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
from .query_converters.base import (
    QueryConverter, 
    QueryResult, 
    ConversionResult, 
    ConversionStatus
)
from .query_executor import QueryExecutor
from .utils.llm import gemini_inference


class QueryConversionOrchestrator:
    """Manages query conversion with iterative refinement."""
    
    def __init__(
        self,
        converter: QueryConverter,
        source_connection: Dict,
        dest_connection: Dict,
        source_schema: str,
        dest_schema: str,
        max_attempts: int = 5,
        num_workers: int = 1
    ):
        """Initialize query conversion orchestrator.
        
        Args:
            converter: QueryConverter instance
            source_connection: Source database connection info
            dest_connection: Destination database connection info
            source_schema: Source database schema
            dest_schema: Destination database schema
            max_attempts: Maximum attempts per query (default: 3)
            num_workers: Number of parallel workers (default: 1)
        """
        self.converter = converter
        self.source_connection = source_connection
        self.dest_connection = dest_connection
        self.source_schema = source_schema
        self.dest_schema = dest_schema
        self.max_attempts = max_attempts
        self.num_workers = num_workers
        self.executor = QueryExecutor()
        self.print_lock = Lock()
    
    def convert_query(self, source_query: str, question_id: str = "", verbose: bool = True) -> ConversionResult:
        """Convert a single query with iterative refinement.
        
        Args:
            source_query: Query in source dialect
            question_id: Optional question ID for logging
            verbose: Whether to print progress messages
            
        Returns:
            ConversionResult with final status and converted query
        """
        def safe_print(*args, **kwargs):
            """Thread-safe printing."""
            if verbose:
                with self.print_lock:
                    print(*args, **kwargs)
        
        safe_print(f"\n{'=' * 80}")
        safe_print(f"Converting query (ID: {question_id}):")
        safe_print(f"{source_query[:100]}{'...' if len(source_query) > 100 else ''}")
        safe_print(f"{'=' * 80}")
        
        # Execute source query
        safe_print("\n1. Executing source query...")
        source_result = self._execute_source_query(source_query)
        
        if source_result.error:
            safe_print(f"✗ Source query execution failed: {source_result.error}")
            return ConversionResult(
                status=ConversionStatus.UNABLE_TO_MATCH,
                converted_query="",
                source_result=source_result,
                reason=f"Source query execution failed: {source_result.error}",
                attempts=0
            )
        
        safe_print(f"✓ Source query executed: {source_result.total_rows} rows")
        
        # Iterative conversion loop
        converted_query = None
        dest_result = None
        last_notes = ""  # Track last notes from LLM
        attempt_history = []  # Track all previous attempts
        
        for attempt in range(1, self.max_attempts + 1):
            safe_print(f"\n2. AI Conversion Attempt {attempt}/{self.max_attempts}...")
            
            # Build prompt with full history
            prompt = self.converter.get_conversion_prompt(
                source_schema=self.source_schema,
                dest_schema=self.dest_schema,
                source_query=source_query,
                converted_query=converted_query,
                source_result=source_result,
                dest_result=dest_result,
                attempt=attempt,
                max_attempts=self.max_attempts,
                attempt_history=attempt_history
            )
            
            # Call AI (ReAct style)
            try:
                response = gemini_inference(prompt, temperature=0.2, enforce_json=True)
                result_json = json.loads(response)
                
                # Check if AI has finished (result_matched or unable_to_match)
                conversion_finished = result_json.get('conversion_finished', '')
                notes = result_json.get('notes', '')
                last_notes = notes  # Track last notes for potential exhausted retry
                
                if conversion_finished:
                    safe_print(f"   AI Decision: {conversion_finished}")
                    safe_print(f"   Notes: {notes}")
                    
                    # Parse the final status
                    try:
                        status = ConversionStatus(conversion_finished)
                    except ValueError:
                        safe_print(f"✗ Invalid conversion_finished value: {conversion_finished}")
                        continue
                    
                    # If result_matched but no converted_query provided, use source query
                    # (LLM is saying the query doesn't need conversion)
                    final_query = converted_query if converted_query else source_query
                    
                    # Return the final result
                    return ConversionResult(
                        status=status,
                        converted_query=final_query,
                        source_result=source_result,
                        dest_result=dest_result,
                        reason=notes,
                        attempts=attempt
                    )
                
                # AI wants to try a new conversion
                new_query = result_json.get('converted_query', '')
                if new_query:
                    converted_query = new_query
                    safe_print(f"   AI Action: Trying new conversion")
                    safe_print(f"   Notes: {notes}")
                    
                    # Execute the new converted query
                    safe_print(f"\n3. Executing converted query...")
                    dest_result = self._execute_dest_query(converted_query)
                    
                    if dest_result.error:
                        safe_print(f"✗ Execution failed: {dest_result.error}")
                    else:
                        safe_print(f"✓ Executed: {dest_result.total_rows} rows")
                        
                        # Check if results match
                        matches, match_reason = self.converter.compare_results(
                            source_result, dest_result
                        )
                        if matches:
                            safe_print(f"✓ Results MATCH! {match_reason}")
                            return ConversionResult(
                                status=ConversionStatus.RESULT_MATCHED,
                                converted_query=converted_query,
                                source_result=source_result,
                                dest_result=dest_result,
                                reason=match_reason,
                                attempts=attempt
                            )
                        else:
                            safe_print(f"✗ Results differ: {match_reason}")
                    
                    # Record this attempt in history
                    attempt_history.append({
                        'attempt': attempt,
                        'query': converted_query,
                        'result': dest_result,
                        'notes': notes
                    })
                    
                    # Continue to next attempt with updated dest_result
                    continue
                else:
                    safe_print(f"✗ AI response missing both 'conversion_finished' and 'converted_query'")
                    continue
                
            except Exception as e:
                safe_print(f"✗ AI conversion failed: {e}")
                continue
        
        # Max attempts reached without success
        return ConversionResult(
            status=ConversionStatus.EXHAUSTED_RETRY,
            converted_query=converted_query or "",
            source_result=source_result,
            dest_result=dest_result,
            reason=last_notes or f"Max attempts ({self.max_attempts}) reached without achieving match",
            attempts=self.max_attempts
        )
    
    def convert_queries(
        self,
        queries: List[Tuple[str, str]],  # List of (question_id, query)
        output_file: Optional[Path] = None
    ) -> Dict[str, ConversionResult]:
        """Convert multiple queries.
        
        Args:
            queries: List of tuples (question_id, query)
            output_file: Optional file to save conversion results
            
        Returns:
            Dict mapping (question_id, original query) to ConversionResult
        """
        results = {}
        
        print(f"\n{'=' * 80}")
        print(f"QUERY CONVERSION: Converting {len(queries)} queries")
        if self.num_workers > 1:
            print(f"Using {self.num_workers} parallel workers")
        print(f"{'=' * 80}")
        
        if self.num_workers == 1:
            # Sequential processing
            for i, (question_id, query) in enumerate(queries, 1):
                print(f"\n\n{'#' * 80}")
                print(f"QUERY {i}/{len(queries)} (Question ID: {question_id})")
                print(f"{'#' * 80}")
                
                result = self.convert_query(query, question_id=question_id, verbose=True)
                results[(question_id, query)] = result
                
                # Print summary
                print(f"\n{'=' * 80}")
                print(f"RESULT: {result.status.value}")
                print(f"Attempts: {result.attempts}")
                if result.reason:
                    print(f"Reason: {result.reason}")
                if result.status == ConversionStatus.RESULT_MATCHED:
                    print(f"✓ SUCCESS: Query converted and verified!")
                elif result.status == ConversionStatus.EXHAUSTED_RETRY:
                    print(f"⚠ WARNING: Max attempts reached without match")
                else:
                    print(f"✗ ERROR: Unable to match query")
                print(f"{'=' * 80}")
        else:
            # Parallel processing
            completed = 0
            total = len(queries)
            
            def convert_with_progress(question_id: str, query: str, index: int) -> Tuple[str, str, ConversionResult]:
                """Convert query and return with identifiers."""
                result = self.convert_query(query, question_id=question_id, verbose=False)
                return (question_id, query, result)
            
            with ThreadPoolExecutor(max_workers=self.num_workers) as executor:
                # Submit all tasks
                future_to_query = {
                    executor.submit(convert_with_progress, qid, q, i): (qid, q, i)
                    for i, (qid, q) in enumerate(queries, 1)
                }
                
                # Process completed tasks
                for future in as_completed(future_to_query):
                    qid, query, result = future.result()
                    results[(qid, query)] = result
                    completed += 1
                    
                    # Print progress
                    with self.print_lock:
                        if result.status == ConversionStatus.RESULT_MATCHED:
                            status_symbol = "✓"
                        elif result.status == ConversionStatus.EXHAUSTED_RETRY:
                            status_symbol = "⚠"
                        else:
                            status_symbol = "✗"
                        print(f"[{completed:2d}/{total}] {status_symbol} Query {qid}: {result.status.value}")
        
        # Save results if requested
        if output_file:
            self._save_results_with_ids(results, output_file)
        
        # Print final summary
        self._print_summary(results)
        
        return results
    
    @staticmethod
    def load_queries_from_csv(csv_file: Path) -> List[Tuple[str, str]]:
        """Load queries from CSV file.
        
        Args:
            csv_file: Path to CSV file with columns: question_id, source_query
            
        Returns:
            List of tuples (question_id, query)
        """
        queries = []
        with open(csv_file, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                question_id = row.get('question_id', '')
                query = row.get('source_query', '')
                if query:
                    queries.append((question_id, query))
        return queries
    
    def _execute_source_query(self, query: str) -> QueryResult:
        """Execute query on source database."""
        if self.converter.source_dialect == 'sqlite':
            return self.executor.execute_sqlite(
                self.source_connection['path'],
                query
            )
        elif self.converter.source_dialect == 'postgresql':
            return self.executor.execute_postgresql(
                self.source_connection['host'],
                self.source_connection['port'],
                self.source_connection['user'],
                self.source_connection['password'],
                self.source_connection['database'],
                query
            )
        elif self.converter.source_dialect == 'bigquery':
            return self.executor.execute_bigquery(
                self.source_connection['project_id'],
                self.source_connection['dataset_id'],
                query
            )
        else:
            raise NotImplementedError(f"Source dialect {self.converter.source_dialect} not supported yet")
    
    def _execute_dest_query(self, query: str) -> QueryResult:
        """Execute query on destination database."""
        if self.converter.dest_dialect == 'postgresql':
            return self.executor.execute_postgresql(
                self.dest_connection['host'],
                self.dest_connection['port'],
                self.dest_connection['user'],
                self.dest_connection['password'],
                self.dest_connection['database'],
                query
            )
        elif self.converter.dest_dialect == 'mysql':
            return self.executor.execute_mysql(
                self.dest_connection['host'],
                self.dest_connection['port'],
                self.dest_connection['user'],
                self.dest_connection['password'],
                self.dest_connection['database'],
                query
            )
        elif self.converter.dest_dialect == 'spanner':
            return self.executor.execute_spanner(
                self.dest_connection['project_id'],
                self.dest_connection['instance_id'],
                self.dest_connection['database_id'],
                query
            )
        elif self.converter.dest_dialect == 'bigquery':
            return self.executor.execute_bigquery(
                self.dest_connection['project_id'],
                self.dest_connection['dataset_id'],
                query
            )
        else:
            raise NotImplementedError(f"Destination dialect {self.converter.dest_dialect} not supported yet")
    
    def _save_results_with_ids(self, results: Dict[Tuple[str, str], ConversionResult], output_file: Path):
        """Save conversion results to file (CSV or JSON) with question IDs."""
        if str(output_file).endswith('.csv'):
            self._save_results_csv_with_ids(results, output_file)
        else:
            self._save_results_json_with_ids(results, output_file)
        
        print(f"\n✓ Results saved to: {output_file}")
    
    def _save_results_csv_with_ids(self, results: Dict[Tuple[str, str], ConversionResult], output_file: Path):
        """Save conversion results to CSV file with question IDs."""
        with open(output_file, 'w', newline='', encoding='utf-8') as f:
            fieldnames = [
                'question_id',
                'source_query',
                'converted_query',
                'conversion_result',
                'attempts',
                'source_rows',
                'dest_rows',
                'reason'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for (question_id, query), result in results.items():
                writer.writerow({
                    'question_id': question_id,
                    'source_query': query,
                    'converted_query': result.converted_query,
                    'conversion_result': result.status.value,
                    'attempts': result.attempts,
                    'source_rows': result.source_result.total_rows if result.source_result else 0,
                    'dest_rows': result.dest_result.total_rows if result.dest_result else 0,
                    'reason': result.reason or ''
                })
    
    def _save_results_json_with_ids(self, results: Dict[Tuple[str, str], ConversionResult], output_file: Path):
        """Save conversion results to JSON file with question IDs."""
        output_data = []
        
        for (question_id, query), result in results.items():
            output_data.append({
                'question_id': question_id,
                'source_query': query,
                'converted_query': result.converted_query,
                'status': result.status.value,
                'reason': result.reason,
                'attempts': result.attempts,
                'source_rows': result.source_result.total_rows if result.source_result else 0,
                'dest_rows': result.dest_result.total_rows if result.dest_result else 0
            })
        
        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)
    
    def _print_summary(self, results: Dict):
        """Print summary of all conversions."""
        total = len(results)
        matched = sum(1 for r in results.values() if r.status == ConversionStatus.RESULT_MATCHED)
        unable = sum(1 for r in results.values() if r.status == ConversionStatus.UNABLE_TO_MATCH)
        exhausted = sum(1 for r in results.values() if r.status == ConversionStatus.EXHAUSTED_RETRY)
        
        print(f"\n\n{'=' * 80}")
        print(f"FINAL SUMMARY")
        print(f"{'=' * 80}")
        print(f"Total queries: {total}")
        print(f"✓ Result Matched: {matched} ({matched/total*100:.1f}%)")
        print(f"✗ Unable to Match: {unable} ({unable/total*100:.1f}%)")
        print(f"⚠ Exhausted Retry: {exhausted} ({exhausted/total*100:.1f}%)")
        print(f"{'=' * 80}\n")


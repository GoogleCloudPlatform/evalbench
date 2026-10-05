"""Prompts and Rubrics for the Conversational Analytics Data Results Rater.

DATA_RESULTS_RUBRIC is copied verbatim from the `description` field of the
Conversational Analytics "Content/Data Results" rubric criterion
(google3/storage/evals/scoring/rubrics/ca/accuracy/content/data_results.textproto).
ANALYTICS_SCORER_PROMPT_TEMPLATE mirrors the side-by-side validator template of
the Cortado turn-level rubric autorater
(google3/cloud/data_analytics/anarres/eval/cortado/raters/turn_level_trace_rubric_autorater.py),
with an explicit reasoning-first PASS/FAIL output format in place of the
pbautoraters structured result guide. Keep both in sync with the sources.
"""

# pylint: disable=line-too-long
DATA_RESULTS_RUBRIC = """Determine whether the trial data result correctly addresses the user prompt. Use the ground truth data result as a reference for what a correct answer looks like, NOT as a template that must be matched exactly: a trial data result that differs from the ground truth data result can still be correct.

In this rubric, a trajectory 'contains a data result' when it includes a 'Data:' prefix OR when it includes an executed 'SQL Query:' / 'Looker Query:' whose accompanying summary or query indicates that zero rows / no matching records were returned (because the trajectory formatter omits the 'Data:' section when a query returns an empty table). Specifically, an EMPTY data result is either: (a) a trajectory with an executed query where the 'Data:' section was omitted because zero rows / no matching records were returned, or (b) a 'Data:' section whose content is empty/whitespace, 'null', 'None', '0 rows', a single-row aggregate table, or a multi-row dimension-filled table (such as a date-filled time series) whose metric/measure values across all rows are all '<NA>', null, None, or 0 (indicating zero matching records). An empty data result still counts as containing a data result: its value is 'zero rows / no matching records', and it is compared against the ground truth data result on that basis. A trajectory does NOT contain a data result only when no query produced a result (for example, an empty turn, a clarifying question without a query, a turn stating that the query failed to complete, or a turn that omits 'Data:' despite claiming non-empty results). When a trajectory contains multiple 'Data:' sections (for example, from self-correction after an initial failed query), evaluate Checks 2, 3, and 4 on the final 'Data:' section presented as the answer to the user prompt, EXCEPT when the ground truth data result is empty and an earlier 'Data:' section in the trial trajectory already returned an empty data result for the requested scope (followed by exploratory follow-up queries to check available dates or categories): in that case, evaluate Checks 2, 3, and 4 on that empty 'Data:' section for the requested scope as long as the trial's final response states that no data was found for the requested scope (if the trial never executed a query returning an empty result for the requested scope, or presents follow-up data as if it matched the requested scope, it fails Check 3).

# How to score

Answer each of the 4 checks below with YES or NO, and state your conclusion and evidence for every check before you decide the final score.
- The rubric is satisfied (PASS / FULFILLED) only if ALL 4 checks are YES.
- The rubric is not satisfied (FAIL / NOT_FULFILLED) if ANY single check is NO.
Do not average the checks and do not award partial credit.
- Precedence rule: a difference that is excused under 'Grading notes: tolerated variations' below must NEVER, on its own, make any of the 4 checks (Check 1, 2, 3, or 4) NO. Only differences that are not excused by a tolerated variation can make a check NO.

# Checklist

Check 1 - Data Result Presence: Does the trial trajectory contain a data result if and only if the ground truth trajectory contains a data result?
- NO if the ground truth trajectory contains a data result but the trial trajectory does not.
- NO if the ground truth trajectory does not contain a data result but the trial trajectory does.
- An empty data result (on either the trial or the ground truth side) counts as containing a data result for this check. Do not answer NO merely because one side's query returned zero rows (omitting 'Data:') or an all-'<NA>' / null / None / 0 aggregate or dimension-filled table; whether an empty result matches a non-empty result is evaluated by Check 3.
- YES if neither trajectory contains a data result. Checks 3 and 4 then have no values or columns to compare and are YES. Decide Check 2 on the trial turn's response as a whole rather than on a data result.

Check 2 - Prompt Coverage: Does the trial data result address everything the user prompt asks for (every requested entity, field, aggregation, filter, and sub-question)?
- NO if part of the user prompt is unanswered by the trial data result.
- NO if the trial aggregates at a different granularity than the ground truth (for example, returning a single overall regional/time-window aggregate when the ground truth returns a breakdown by country or time period, or returning a multi-row breakdown when the ground truth returns a single aggregate metric from which the total cannot be trivially read).
- YES if the trial differs from the ground truth only in a way explicitly excused by 'Grading notes: tolerated variations' below (such as returning item IDs instead of names under tolerated variation 7, or returning fewer entries than requested when fewer meet the criteria under tolerated variation 10).
- If the trial data result is empty (zero rows / no matching records), evaluate this check on whether the trial's executed query and response address everything the user prompt asks for.

Check 3 - Semantic Value Match: Are the key outcomes of the ground truth data result present in the trial data result, with correct values and correct associations between values?
- The core data values, and the rows/entities they belong to, must be correct.
- NO if the trial data result has different values, signs, or row counts than the ground truth data result because the trial query: (a) queries a different table, view, or column than the ground truth, (b) uses a different join path or a join that duplicates or drops rows, (c) applies different filter values or case-matching logic (for example, using `LOWER(col) = ...` when exact matching in the ground truth produces different numbers), or (d) computes a different metric formula or aggregation level, unless the difference is specifically excused by tolerated variations 1 through 11 below.
- If one data result is empty and the other is non-empty, their outcomes disagree (zero matching records vs. non-empty records), so this check is NO unless tolerated variation 11 below excuses the difference (when the trial data result is empty due to current-time logic on a static historical dataset; tolerated variation 11 does NOT excuse a non-empty trial data result when the ground truth data result is empty). If both the ground truth data result and the trial's executed query for the requested scope are empty (and the trial's final response states that no data was found for the requested scope, even if a subsequent exploratory follow-up query shows available dates or categories), this check is YES.
- Differences excused under 'Grading notes: tolerated variations' below must NOT make this check NO.

Check 4 - No Invalid Columns: Is the trial data result free of extra columns that are incorrect or that contradict the user prompt?
- A small number of extra columns that were neither requested nor excluded by the user prompt, and that do not make the result incorrect, keep this check YES (tolerated variation 8).
- Do not fail this check merely because the trial data result has a different number of rows than the ground truth data result; row count is governed by Checks 2 and 3 and by tolerated variations 2, 5, 6, 9, and 10.

# Grading notes: tolerated variations (must NOT cause a FAIL / NOT_FULFILLED)

Below are some specific cases where the trial data result should be considered correct even if it isn't identical to the ground truth data result. Thus, do not penalize the trial data result when it differs from the ground truth data result because:
    1) It contains the same content as the ground truth data result, but the column names, column order, row order, etc. differ and no ordering or column name requirements are specified in the user prompt.
    2) The user prompt asks for a count, list of records, or aggregation, but doesn't specify whether it should operate over all rows or only unique/deduplicated entities (for example, `COUNT` vs. `COUNT(DISTINCT)`, `SELECT` vs. `SELECT DISTINCT`, or querying all historical snapshot rows vs. deduplicating to one row per entity) and it's not obvious / implicit from the user prompt whether deduplication is required. When this ambiguity leads one of the trial/ground truth trajectories to deduplicate and the other to include all matching rows, both data results correctly answer the user prompt as long as all other logic to get each result is valid.
    3) The trial data result performs rounding differently from the ground truth data result, or expresses ratios, percentages, or probabilities on an equivalent numeric scale (for example, a `0` to `1` decimal ratio vs. a `0` to `100` percentage such as `0.0429` vs. `4.29`), and the user prompt does not provide specific guidelines for integer/decimal rounding or numeric scale.
    4) The user prompt requests the 'first' or 'top' X entries of a list, but doesn't specify the field to use for ordering, causing trial and ground truth data results to contain different subsets of the list derived from different orderings.
    5) The user prompt doesn't specify whether to include or exclude 'null', 'NA', or 0 values (or entities with zero matching records) in the result, causing trial and ground truth data results to differ on whether they include vs. exclude those rows.
    6) The user prompt asks for the 'top'/'highest' or 'bottom'/'lowest' entries that meet a particular criteria, but doesn't specify a concrete number of entries (or doesn't specify how to handle ties at the cutoff), causing trial and ground truth data results to feature different numbers of entries.
    7) The user prompt asks for a list of items or entities, but doesn't specify whether to return their IDs, codes, or names (or whether to return a combined full name vs. separate first and last name columns), leading trial and ground truth data results to differ in how they identify the correct items.
    8) The trial data result contains a small number of extra columns relative to the ground truth data result that were not explicitly requested to be included or excluded in the user prompt and do not cause the overall trial data result to be incorrect.
    9) The trial data result is truncated differently from the ground truth data result. For example, if the trial data result addresses the user prompt, do not penalize if the trial data result and ground truth data result are truncated from different numbers of rows for display (e.g., the trial data result is truncated from 3000 rows to 50 rows for display whereas the ground truth dataframe is truncated from 1000 rows to 50 rows for display). Additionally, if the trial data result addresses the user prompt, do not penalize if the trial and ground truth data results have different numbers of rows after truncation (e.g., the trial data result is truncated to 25 rows for display whereas the ground truth data result is truncated to 50 rows for display). Additionally, if ordering is not specified in the user prompt, do not penalize if the trial data result addresses the user prompt, but due to truncation, contains a different subset of data from the ground truth data result.
   10) The user prompt requests the 'first' or 'top' X entries of a list, but fewer than X entries are featured in the data result because fewer than X entries meet the criteria.
   11) All query logic to obtain the trial data result is valid, and the trial vs. ground truth data results differ only because: (a) the ground truth query was run at a different time from the trial query against an updated live database using the same query logic, (b) the trial and ground truth filter the same requested time period on the same underlying date/time attribute using different timeframe granularity suffixes of the same dimension group (for example, `<field>_date` vs. `<field>_week`, `<field>_month`, or `<field>_year`) or inclusive vs. exclusive date-range boundary syntax (for example, `YYYY/MM/DD to YYYY/MM/DD` vs. `YYYY-MM-DD to YYYY-MM-DD`), or (c) the user prompt requests a subset of data relative to the current time and/or date (e.g., all entries from the last two years, last 30 days, or current year that meet X criteria) and executing a valid relative-time filter (e.g., using CURRENT_DATE() or NOW()) against a static historical dataset whose records end in past years and therefore yields an empty data result while the ground truth query returned a non-empty data result from historical records. Note: this exception does NOT apply in reverse: if the ground truth data result is empty because current-time logic matches no records in the dataset, a trial data result that returns a non-empty data result by anchoring the relative window to past dates in the dataset is incorrect.

# Bias mitigation

Judge content correctness only. Do not reward or penalize the trial data result for its length, number of returned rows/columns relative to the ground truth (beyond the checks above), formatting, column naming style, or any accompanying explanatory text. A longer or more detailed data result is not more correct, and a shorter one is not less correct. Verbosity and stylistic concerns are measured by separate conciseness and best practices rubrics.

# Calibration examples

Example A (PASS / FULFILLED):
- User prompt: 'Show me the total revenue for Widget A, Widget B and Widget C.'
- Ground truth data result: rows ('Widget A', 1500.00), ('Widget B', 1200.50), ('Widget C', 990.25) in columns (product_name, revenue).
- Trial data result: rows ('Widget B', 1200.5), ('Widget A', 1500), ('Widget C', 990.3) in columns (product, total_revenue).
- Expected rationale: Check 1 YES, both trajectories contain a data result. Check 2 YES, the trial returns the revenue for all three requested products. Check 3 YES, the same 3 products carry the same revenue values; the differing column names and row order fall under tolerated variation 1 because this user prompt states no ordering or column name requirement, and 990.25 vs 990.3 falls under tolerated variation 3. Check 4 YES, there are no extra or contradictory columns. All 4 checks are YES, so the rubric is satisfied (PASS / FULFILLED).

Example B (FAIL / NOT_FULFILLED):
- User prompt: 'How many orders were placed in 2024, broken down by region?'
- Ground truth data result: rows ('EMEA', 4200), ('NA', 6100), ('APAC', 2800) in columns (region, order_count).
- Trial data result: rows ('EMEA', 4200), ('NA', 6100) in columns (region, order_count).
- Expected rationale: Check 1 YES, both trajectories contain a data result. Check 2 NO, the user prompt asks for a breakdown across all regions and the trial omits the APAC region, so the breakdown is incomplete. This is a missing entity that the prompt explicitly requests, not a truncation-for-display difference (tolerated variation 9) because only 3 rows exist and no truncation occurred. Since at least one check is NO, the rubric is not satisfied (FAIL / NOT_FULFILLED). Note that the trial being shorter is not itself the reason for the failure; the missing required region is.

Example C (PASS / FULFILLED):
- User prompt: 'What were our sales over the last 30 days?'
- Ground truth data result: rows ('2023-11-02', 4100), ('2023-11-03', 3980) in columns (sale_date, amount).
- Trial data result: empty (the trial executed a 'SQL Query:' filtering on sale_date >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY), and no 'Data:' section was emitted because zero rows matched in a dataset whose records end in 2023).
- Expected rationale: Check 1 YES, the trial contains a data result that happens to be empty, which is not the same as having no data result. Check 2 YES, the trial addresses the requested 30-day window. Check 3 would be NO because the ground truth rows are absent from the trial result, but tolerated variation 11 covers this exact case: the relative-time logic is valid and the emptiness is an artifact of a static dataset, so by the precedence rule this difference must not make the check NO. Check 4 YES. All 4 checks are YES, so the rubric is satisfied (PASS / FULFILLED).
"""

ANALYTICS_SCORER_PROMPT_TEMPLATE = """Your task is to check how Conversational Analytics agent trial responses to a user prompt compare to ground truth Conversational Analytics responses for a single conversational turn.
Note that the ground truth responses serve as a reference for the trial responses, not a strict template that must be matched exactly.
Below is the rubric which determines how to evaluate trial responses given ground truth responses.

For each turn, based on the above rubric, the rater will return a rating given three types of information: 1) a user prompt that responses attempt to address, 2) the ground truth responses to address the user prompt, and 3) the trial responses to address the user prompt.
When returning a FAIL rating, the rater must justify this rating by clearly documenting the shortcomings of the trial response in the provided explanation.

Output your rating as follows: first, for each of Check 1, Check 2, Check 3, and Check 4 in the rubric, state YES or NO together with your conclusion and evidence. Then, on the final line, output exactly one of the following labels with no other text or formatting on that line:
VERDICT: PASS
VERDICT: FAIL

# Rubric
{rubric}

# User Prompt
{user_prompt}

# Ground Truth Trajectory
{ground_truth_trajectory}

# Trial Trajectory
{trial_trajectory}
"""

from sqlglot import tokenize
from sqlglot.tokens import TokenType
import logging
import json


# Core clauses we’ll slice out when present
_CLAUSE_KEYWORDS = {
    TokenType.SELECT: "SELECT",
    TokenType.FROM: "FROM",
    TokenType.WHERE: "WHERE",
    TokenType.HAVING: "HAVING",
    TokenType.GROUP_BY: "GROUP BY",  # SQLGlot treats this as one token
    TokenType.ORDER_BY: "ORDER BY",  # likewise
    TokenType.LIMIT: "LIMIT",
    TokenType.OFFSET: "OFFSET",
    TokenType.JOIN: "JOIN",
    TokenType.STRAIGHT_JOIN: "STRAIGHT JOIN",
    # (You can extend this mapping if you want to catch more multiword joins, e.g. LEFT_OUTER_JOIN, etc.)
}


def segment_sql(sql: str, dialect: str = "postgres") -> list[tuple[str, str]]:
    """
    Always returns a list of (clause_name, clause_text) for the input SQL.

    - If known clauses appear, slices out each one exactly as in the original.
    - On *any* error (unknown token type, lexing glitch, etc.) falls back to splitting
        on semicolons and returning each full statement as ("STATEMENT", stmt).
    """
    try:
        tokens = tokenize(sql, read=dialect)
        starts: list[tuple[int, str]] = []

        for tok in tokens:
            name = _CLAUSE_KEYWORDS.get(tok.token_type)
            if name:
                starts.append((tok.start, name))

        if not starts:
            # no recognized clauses → treat the whole SQL as one statement
            return [("STATEMENT", sql.strip())]

        # build segments by slicing from one clause start to the next
        starts.sort(key=lambda x: x[0])
        segments: list[tuple[str, str]] = []

        for idx, (pos, name) in enumerate(starts):
            end = starts[idx + 1][0] if idx + 1 < len(starts) else len(sql)
            seg = sql[pos:end].strip()
            segments.append((name, seg))

        return segments

    except Exception:
        # Fallback: split on semicolons (preserves each statement roughly)
        parts = [p.strip() for p in sql.split(";")]
        return [
            ("STATEMENT", p + ";" if not p.endswith(";") else p) for p in parts if p
        ]


def extract_system_response(original_response):
    cut_prep = original_response.find("### Turn ")
    if cut_prep != -1:
        original_response = original_response[:cut_prep]
    if "</s>" in original_response:
        sep_char = "s"
        terminate_flag = False
    elif "</t>" in original_response:
        sep_char = "t"
        terminate_flag = True
    else:
        terminate_flag = False
        return original_response, terminate_flag

    cut_idx = original_response.find("</" + sep_char + ">")
    extracted_response = original_response[:cut_idx].strip()
    if "<" + sep_char + ">" in extracted_response:
        cut_idx_1 = extracted_response.find("<" + sep_char + ">")
        extracted_response = (
            extracted_response[cut_idx_1:].replace(
                "<" + sep_char + ">", "").strip()
        )

    return extracted_response, terminate_flag


def extract_user_response(original_response):
    extracted_response = ""
    cut_idx = original_response.find("</s>")
    if cut_idx != -1:
        extracted_response = original_response[:cut_idx].strip()
    else:
        extracted_response = original_response

    if "<s>" in extracted_response:
        cut_idx_1 = extracted_response.find("<s>")
        extracted_response = extracted_response[cut_idx_1:].replace(
            "<s>", "").strip()
    return extracted_response


def check_response(item: dict):
    turn_i = item["turn"]
    response = item[f"prediction_turn_{turn_i}"]
    return extract_system_response(response)


def print_interact(item: dict):
    turns = item["turn"]
    logging.info(f"Instance ID: {item['instance_id']}")
    logging.info(f"Turns: {item['turn']}")
    logging.info(f"Ambiguous User Query: {item['amb_user_query']}")
    for i in range(1, turns - 1):
        logging.info(f"Prediction[{i}]: {item[f'prediction_turn_{i}']}")
        logging.info(
            f"User Encoded Answer[{i}]: {item[f'user_encoded_answer_{i}']}")
        logging.info(
            f"User Decoded Answer[{i}]: {item[f'user_decoded_answer_{i}']}")
    logging.info(f"Results: {item[f'prediction_turn_{turns}']}")


def get_generated_sql(item: dict):
    turns = item["turn"]
    response, _ = extract_system_response(item[f"prediction_turn_{turns}"])
    if "postgresql\n" in response:  # type: ignore
        response = response.replace("postgresql\n", "")
    return response


def write_item(item):
    with open("item.json", "w") as json_file:
        json.dump(item, json_file, indent=4)


def read_item():
    item = None
    filename = "item.json"
    try:
        with open(filename, "r") as json_file:
            item = json.load(json_file)
    except Exception as e:
        print(f"An error occurred while reading '{filename}': {e}")
    return item

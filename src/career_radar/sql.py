"""Small SQL-file helpers used by the project scripts."""


def statements_from(sql_text: str) -> list[str]:
    """Split a SQL file into executable statements.

    We cannot use a plain ``sql_text.split(";")`` because semicolons can appear
    inside SQL strings, such as ``string_to_array(skills_mentioned, ';')``.
    This lightweight splitter understands the simple SQL we keep in this repo:
    single-quoted strings, double-quoted identifiers, and ``--`` line comments.
    """

    statements = []
    current_statement = []
    in_single_quote = False
    in_double_quote = False
    in_line_comment = False
    index = 0

    while index < len(sql_text):
        char = sql_text[index]
        next_char = sql_text[index + 1] if index + 1 < len(sql_text) else ""

        if in_line_comment:
            if char == "\n":
                in_line_comment = False
                current_statement.append(char)
            index += 1
            continue

        if not in_single_quote and not in_double_quote and char == "-" and next_char == "-":
            in_line_comment = True
            index += 2
            continue

        if char == "'" and not in_double_quote:
            current_statement.append(char)
            if in_single_quote and next_char == "'":
                current_statement.append(next_char)
                index += 2
                continue
            in_single_quote = not in_single_quote
            index += 1
            continue

        if char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
            current_statement.append(char)
            index += 1
            continue

        if char == ";" and not in_single_quote and not in_double_quote:
            statement = "".join(current_statement).strip()
            if statement:
                statements.append(statement)
            current_statement = []
            index += 1
            continue

        current_statement.append(char)
        index += 1

    final_statement = "".join(current_statement).strip()
    if final_statement:
        statements.append(final_statement)

    return statements

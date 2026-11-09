"""Database access; transaction ownership stays with the caller."""


def interactive_query(owner):
    text = "".join(
        block.get("text", "") for block in owner.get("content", []) if block.get("type") == "text"
    )
    return text


def interactive_query_2(owner):
    return owner.get("id", "")

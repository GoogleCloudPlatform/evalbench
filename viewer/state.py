import mesop as me

@me.stateclass
class State:
    selected_directory: str = ""
    selected_tab: str = "Dashboard"
    conversation_index: int = 0
    eval_summaries: str = ""
    eval_id_filter: str = ""
    product_filter: str = ""
    requester_filter: str = ""
    sort_column: str = "date"
    sort_descending: bool = True
    open_dropdown: str = ""
    selected_main_tab: str = "List"
    trends_product_filter: str = ""

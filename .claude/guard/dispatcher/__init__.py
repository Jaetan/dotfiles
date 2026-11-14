"""The guard kit's dispatcher: one process per hook event reads the event once, builds the call model once, and runs
every rule module that names the event and the tool against it, giving one combined verdict (dispatch.py)."""

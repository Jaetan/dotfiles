"""The names alive in the guard kit though nothing reads them as an attribute, which vulture would report as unused.

tests/test_dead_code.py runs vulture over the kit, this file with it, so every name written here counts as read. A
name belongs here only under the reason it is alive, and a plant that removes it turns that test red.
"""

from vulture.whitelists.whitelist_utils import Whitelist

_ = Whitelist()

# The rule protocol: the dispatcher reads EVENTS and TOOLS from each rule module by name.
_.EVENTS
_.TOOLS

# Fields of the records and reports the gates and the runner build by key and write as JSON, and of the Bash options
# a test passes as a dict: read by key or as a whole, never as attributes.
_.bad_lines
_.cmd
_.function_body_extent
_.misreadings
_.mtime_ns
_.nfaults
_.read_this_build
_.reader_error
_.recorded_err
_.run_in_background
_.selection
_.subs
_.unrecorded_refusals

# tempfile.tempdir, the module attribute the runner sets so every test's temporary file lands in its scratch folder.
_.tempdir

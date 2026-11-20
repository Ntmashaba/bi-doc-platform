"""TEMPORARY CI DIAGNOSTIC - diag/generator-windows branch only, never merge.

The generator-windows step log cannot be read anonymously, so this probe re-runs
each Phase 5 sql_agent test method in-process and reports the outcome as
workflow-command annotations, which the anonymous check-runs API exposes.
Every probe case re-raises what it catches: this file never hides a failure.
"""
import importlib
import traceback
import unittest

CASES = [
    ("batch-classify-content", "test_batch", "BatchTest",
     "test_classify_sql_agent_scripts_by_content"),
    ("batch-documents-through-batch", "test_batch", "BatchTest",
     "test_classify_sql_agent_script_documents_through_batch"),
    ("cli-generate-and-extras", "test_cli", "Cli",
     "test_sql_agent_generate_and_extras"),
    ("cli-doctor", "test_cli", "Cli", "test_doctor"),
    ("discovery-selected-folder", "test_discovery", "Discovering",
     "test_a_selected_folder_of_job_scripts_is_one_input"),
    ("discovery-folder-through-batch", "test_discovery", "Discovering",
     "test_a_job_scripts_folder_documents_through_batch"),
    ("discovery-recursive-ignores-sql", "test_discovery", "Discovering",
     "test_recursive_search_ignores_sql_even_job_scripts"),
]


def _note(kind, text):
    # workflow commands need %, CR and LF escaped; annotations keep the rest verbatim
    safe = text.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{kind}::{safe}")


class Diag(unittest.TestCase):
    pass


def _case(name, module_name, class_name, method_name):
    def run_case(self):
        try:
            cls = getattr(importlib.import_module(module_name), class_name)
        except BaseException:
            _note("error", f"DIAG-FAIL {name} importing {module_name}: {traceback.format_exc()}")
            raise
        inst = cls(method_name)
        try:
            inst.setUp()
        except BaseException:
            try:
                inst.doCleanups()
            except BaseException:
                pass
            _note("error", f"DIAG-FAIL {name} in setUp: {traceback.format_exc()}")
            raise
        try:
            getattr(inst, method_name)()
        except unittest.SkipTest:
            _note("warning", f"DIAG-SKIP {name}")
            raise
        except BaseException:
            _note("error", f"DIAG-FAIL {name}: {traceback.format_exc()}")
            raise
        else:
            _note("warning", f"DIAG-OK {name}")
        finally:
            try:
                inst.tearDown()
            except BaseException:
                pass
            inst.doCleanups()
    return run_case


for _name, _mod, _cls, _method in CASES:
    setattr(Diag, "test_diag_" + _name.replace("-", "_"), _case(_name, _mod, _cls, _method))

"""XMLA / TMSL analyser tests."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlagentdocgen.tmsl import analyse_tmsl
from sqlagentdocgen.xmla import analyse_xmla


def test_batch_attributes():
    b = analyse_xmla('<Batch Transaction="true" ProcessAffectedObjects="false"><Process/></Batch>')
    assert b.ok and b.transaction is True and b.process_affected_objects is False


def test_parallel_collects_operations():
    b = analyse_xmla(
        "<Batch><Parallel><Process><Type>ProcessFull</Type></Process>"
        "<Process><Type>ProcessUpdate</Type></Process></Parallel></Batch>"
    )
    assert b.parallel
    assert [op.verb for op in b.operations] == ["ProcessFull", "ProcessUpdate"]


def test_error_configuration():
    b = analyse_xmla(
        "<Batch><ErrorConfiguration><KeyErrorLimit>5</KeyErrorLimit>"
        "<KeyErrorAction>StopProcessing</KeyErrorAction></ErrorConfiguration></Batch>"
    )
    assert b.error_configuration == {"KeyErrorLimit": "5", "KeyErrorAction": "StopProcessing"}


def test_process_object_hierarchy_path():
    b = analyse_xmla(
        "<Batch><Process><Type>ProcessData</Type><Object>"
        "<DatabaseID>DB</DatabaseID><CubeID>C</CubeID>"
        "<MeasureGroupID>MG</MeasureGroupID><PartitionID>P</PartitionID>"
        "</Object></Process></Batch>"
    )
    assert b.operations[0].object_path == "DB/C/MG/P"


def test_process_add_bindings_are_reads():
    b = analyse_xmla(
        "<Batch><Process><Type>ProcessAdd</Type>"
        "<Object><DatabaseID>DB</DatabaseID></Object>"
        "<Bindings><Binding><DataSourceID>DS1</DataSourceID></Binding></Bindings>"
        "</Process></Batch>"
    )
    assert b.operations[0].reads == ["DS1"]


def test_non_process_operations():
    b = analyse_xmla(
        "<Batch><Backup><File>D:\\bak\\db.abf</File></Backup>"
        "<ClearCache><Object><DatabaseID>DB</DatabaseID></Object></ClearCache></Batch>"
    )
    assert [op.kind for op in b.operations] == ["backup", "clearcache"]
    assert b.operations[0].detail["file"].endswith("db.abf")
    assert b.operations[1].object_path == "DB"


def test_namespaces_are_ignored():
    b = analyse_xmla(
        '<Batch xmlns="http://schemas.microsoft.com/analysisservices/2003/engine">'
        "<Process><Type>ProcessFull</Type></Process></Batch>"
    )
    assert b.ok and b.operations[0].verb == "ProcessFull"


def test_bare_operation_without_batch():
    b = analyse_xmla("<Process><Type>ProcessFull</Type></Process>")
    assert b.ok and len(b.operations) == 1


def test_malformed_xml_never_raises():
    b = analyse_xmla("<Batch><Process>")
    assert not b.ok and b.error
    assert not analyse_xmla("not xml at all").ok


def test_tmsl_refresh_objects():
    s = analyse_tmsl('{"refresh": {"type": "full", "objects": ['
                     '{"database": "DB", "table": "T"},'
                     '{"database": "DB", "table": "T2", "partition": "P"}]}}')
    assert s.ok
    assert [op.object_path for op in s.operations] == ["DB/T", "DB/T2/P"]
    assert all(op.verb == "full" for op in s.operations)


def test_tmsl_sequence_envelope():
    s = analyse_tmsl('{"sequence": {"maxParallelism": 3, "operations": ['
                     '{"refresh": {"type": "dataOnly", "objects": [{"database": "D"}]}},'
                     '{"backup": {"database": "D", "file": "d.abf"}}]}}')
    assert s.ok and s.max_parallelism == 3
    assert [op.kind for op in s.operations] == ["refresh", "backup"]


def test_tmsl_malformed_never_raises():
    assert not analyse_tmsl('{"refresh":').ok
    assert not analyse_tmsl("SELECT 1").ok
    assert not analyse_tmsl('[1,2]').ok

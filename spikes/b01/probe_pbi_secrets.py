"""B01 safe-projection probe (Power BI): seed sensitive markers into a model and
report which published representations (HTML, JSON payload) still contain them."""
import json
import sys
import tempfile
from pathlib import Path

import _engines  # noqa: F401
from pbidocgen.model_parser import parse_model
from pbidocgen.renderer import build_payload, render_html

MARKERS = {
    "odbc_password": "SEEDPWD_Odbc_7731",
    "sql_literal": "SEEDSSN_900-11-2222",
    "entered_row": "SEEDROW_Alice_Smith",
    "entered_base64": "i45WSlTSUTJUitWJVjICM41hzFgA",
    "web_token": "SEEDTOKEN_sig_abc123",
}
M = {
    "Creds": f'let S = Odbc.DataSource("Driver={{SQL Server}};Server=db1;Uid=svc;Pwd={MARKERS["odbc_password"]}") in S',
    "Native": 'let S = Sql.Database("finance-sql.corp.local","FinanceDW",[Query="SELECT * FROM dbo.People '
              f"WHERE ssn = '{MARKERS['sql_literal']}'\"]) in S",
    "Entered": f'let S = Table.FromRows({{{{"{MARKERS["entered_row"]}", "555-0100"}}}}, {{"Name","Phone"}}) in S',
    "Compressed": 'let S = Table.FromRows(Json.Document(Binary.Decompress(Binary.FromText('
                  f'"{MARKERS["entered_base64"]}", BinaryEncoding.Base64), Compression.Deflate))) in S',
    "Api": f'let S = Json.Document(Web.Contents("https://api.contoso.com/data?token={MARKERS["web_token"]}")) in S',
}
RAW = {"model": {"name": "Probe", "tables": [
    {"name": n, "columns": [{"name": "A"}], "partitions": [{"name": n, "source": {"type": "m", "expression": e}}]}
    for n, e in M.items()]}}


def main():
    with tempfile.TemporaryDirectory() as d:
        bim = Path(d) / "model.bim"
        bim.write_text(json.dumps(RAW), encoding="utf-8")
        model = parse_model(bim)
        payload = build_payload(model, None, None, "Probe")
        html = Path(d) / "probe.html"
        render_html(payload, html)
        outputs = {"html": html.read_text(encoding="utf-8"), "json": json.dumps(payload)}
    rows = {k: {o: (v in text) for o, text in outputs.items()} for k, v in MARKERS.items()}
    print(json.dumps(rows, indent=1))
    return 1 if any(any(r.values()) for r in rows.values()) else 0


if __name__ == "__main__":
    sys.exit(main())

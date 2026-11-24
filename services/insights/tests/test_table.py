import re
from pathlib import Path

from voltrieve_insights.store import COLUMNS


def test_the_columns_are_those_of_the_table_and_the_rule():
    """The rule drops what it does not declare, so a column that is missing there is lost without an error."""
    bicep = (Path(__file__).parents[3] / "infra/modules/insights.bicep").read_text()
    block = bicep.split("var columns = [")[1].split("\n]")[0]
    assert dict(re.findall(r"name: '(\w+)', type: '(\w+)'", block)) == COLUMNS

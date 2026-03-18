import unittest
from pathlib import Path


class TestDashboardMissingTables(unittest.TestCase):
    def test_query_helpers_and_schema_guard_present(self) -> None:
        app_text = Path("dashboard/app.py").read_text(encoding="utf-8")
        data_access_text = Path("dashboard/data_access.py").read_text(encoding="utf-8")
        self.assertIn("def query_df(sql: str, params: Sequence[Any] = ()) -> pd.DataFrame:", app_text)
        self.assertIn("def q(sql: str) -> pd.DataFrame:", app_text)
        self.assertIn("def _runtime_schema_missing() -> bool:", app_text)
        self.assertIn("except (DatabaseError, sqlite3.OperationalError):", data_access_text)
        self.assertIn("return pd.DataFrame()", data_access_text)

    def test_terminal_name_present(self) -> None:
        text = Path("dashboard/app.py").read_text(encoding="utf-8")
        self.assertIn("Polymarket Terminal", text)


if __name__ == "__main__":
    unittest.main()

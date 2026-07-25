"""
Unit tests for broker-api-changelog-diffing-tool skill.
"""
import unittest
from changelog_differ import BrokerAPIChangelogDiffer, ChangeType, DiffSeverity


class TestBrokerAPIChangelogDiffer(unittest.TestCase):

    def setUp(self):
        self.differ = BrokerAPIChangelogDiffer()
        self.old_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "type": "string", "required": True},
                            {"name": "qty", "type": "integer", "required": True},
                        ]
                    }
                },
                "/v1/positions": {
                    "get": {"parameters": []}
                }
            }
        }

    def test_detect_removed_endpoint(self):
        # /v1/positions removed in new schema
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "type": "string", "required": True},
                            {"name": "qty", "type": "integer", "required": True},
                        ]
                    }
                }
            }
        }

        report = self.differ.diff_schemas(self.old_schema, new_schema, "v1.0", "v2.0")
        self.assertFalse(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 1)
        self.assertEqual(report.changes[0].change_type, ChangeType.REMOVED_ENDPOINT)

    def test_detect_type_mutation_and_new_required_parameter(self):
        # qty type changed integer -> float, and new required parameter 'time_in_force' added
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "type": "string", "required": True},
                            {"name": "qty", "type": "number", "required": True},
                            {"name": "time_in_force", "type": "string", "required": True},
                        ]
                    }
                },
                "/v1/positions": {"get": {"parameters": []}}
            }
        }

        report = self.differ.diff_schemas(self.old_schema, new_schema, "v1.0", "v2.0")
        self.assertFalse(report.is_compatible)

        types = [c.change_type for c in report.changes]
        self.assertIn(ChangeType.TYPE_MUTATION, types)
        self.assertIn(ChangeType.NEW_REQUIRED_PARAMETER, types)

    def test_non_breaking_optional_field(self):
        new_schema = {
            "paths": {
                "/v1/orders": {
                    "post": {
                        "parameters": [
                            {"name": "symbol", "type": "string", "required": True},
                            {"name": "qty", "type": "integer", "required": True},
                            {"name": "client_id", "type": "string", "required": False},
                        ]
                    }
                },
                "/v1/positions": {"get": {"parameters": []}}
            }
        }

        report = self.differ.diff_schemas(self.old_schema, new_schema, "v1.0", "v2.0")
        self.assertTrue(report.is_compatible)
        self.assertEqual(report.total_breaking_changes, 0)
        self.assertEqual(report.changes[0].change_type, ChangeType.ADDED_OPTIONAL_FIELD)


if __name__ == "__main__":
    unittest.main()

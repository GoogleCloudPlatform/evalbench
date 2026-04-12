import unittest
from unittest.mock import patch
from evalbench.databases.alloydb import AlloyDB


class TestAlloyDB(unittest.TestCase):

    @patch('evalbench.databases.postgres.get_adc_user_email')
    def test_adc_init(self, mock_get_email):
        mock_get_email.return_value = "dummy@google.com"

        db_config = {
            "database_path": "projects/p/locations/l/clusters/c/instances/i",
            "database_name": "db",
            "db_type": "alloydb",
            "max_executions_per_minute": 10,
            "nl_config": {}
        }

        alloydb = AlloyDB(db_config)

        # Verify that use_adc is True (inherited from PGDB)
        self.assertTrue(alloydb.use_adc)
        # Verify that username is set to the ADC email
        self.assertEqual(alloydb.username, "dummy@google.com")


if __name__ == '__main__':
    unittest.main()

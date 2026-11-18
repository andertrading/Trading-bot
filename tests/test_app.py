
import unittest

from Bot import app


class TestTradingBotApp(unittest.TestCase):

    def setUp(self):
        self.client = app.test_client()

    def test_home_endpoint(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)

        data = response.get_json()

        self.assertEqual(data["version"], "3.0")
        self.assertEqual(data["mode"], "PAPER_ONLY")

    def test_health_endpoint(self):
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)

        data = response.get_json()

        self.assertEqual(data["status"], "healthy")
        self.assertEqual(data["version"], "3.0")


if __name__ == "__main__":
    unittest.main()

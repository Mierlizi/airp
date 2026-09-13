import unittest

from checkout import checkout
from pricing import discount


class CheckoutTests(unittest.TestCase):
    def test_discount(self):
        self.assertEqual(discount(50, 0.1), 45)

    def test_checkout(self):
        self.assertEqual(checkout(50, 0.1), 53)

    def test_free_shipping(self):
        self.assertEqual(checkout(120), 120)

    def test_invalid_rate(self):
        with self.assertRaises(ValueError):
            discount(50, 2)

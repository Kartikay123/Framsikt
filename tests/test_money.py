import unittest
from decimal import Decimal

from snackbot import CurrencyMismatch, FxRates, Money


class MoneyTests(unittest.TestCase):
    def test_sums_are_exact_to_the_cent(self):
        thirds = [Money.of("333.33", "NOK"), Money.of("333.33", "NOK"), Money.of("333.34", "NOK")]
        total = sum(thirds, Money.zero("NOK"))
        self.assertEqual(total, Money.of(1000, "NOK"))
        self.assertEqual(str(total), "1,000.00 NOK")

    def test_different_currencies_never_mix(self):
        with self.assertRaises(CurrencyMismatch):
            Money.of(100, "NOK") + Money.of(100, "INR")
        with self.assertRaises(CurrencyMismatch):
            Money.of(100, "NOK") < Money.of(100, "INR")

    def test_floats_are_refused(self):
        with self.assertRaises(TypeError):
            Money.of(0.1 + 0.2, "NOK")  # type: ignore[arg-type]

    def test_amounts_are_quantised_half_up(self):
        self.assertEqual(Money.of("12.345", "INR").amount, Decimal("12.35"))

    def test_fx_conversion_for_the_report(self):
        fx = FxRates("NOK", {"INR": "0.125"})
        self.assertEqual(fx.convert(Money.of(2000, "INR"), "NOK"), Money.of(250, "NOK"))
        self.assertEqual(fx.convert(Money.of(250, "NOK"), "INR"), Money.of(2000, "INR"))
        self.assertEqual(fx.convert(Money.of(99, "NOK"), "NOK"), Money.of(99, "NOK"))


if __name__ == "__main__":
    unittest.main()

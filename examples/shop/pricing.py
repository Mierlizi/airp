"""Small demo domain with direct cross-file calls."""


def discount(total: float, rate: float) -> float:
    """Apply a fractional discount, rejecting invalid input."""
    if total < 0 or not 0 <= rate <= 1:
        raise ValueError('Invalid total or discount rate')
    return round(total * (1 - rate), 2)


def shipping(total: float) -> float:
    return 0.0 if total >= 100 else 8.0

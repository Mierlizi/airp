from pricing import discount, shipping


def checkout(total: float, rate: float = 0.0) -> float:
    discounted = discount(total, rate)
    return discounted + shipping(discounted)

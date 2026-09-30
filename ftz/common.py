"""Small helpers shared by the service modules."""
from decimal import ROUND_HALF_EVEN, Decimal

Q4 = Decimal("0.0001")


class ApiError(Exception):
    def __init__(self, errors, status=400):
        self.errors = [errors] if isinstance(errors, str) else list(errors)
        self.status = status
        super().__init__("; ".join(self.errors))


def need(cond, msg, status=400):
    if not cond:
        raise ApiError(msg, status)


def pick(d, keys):
    return {k: (d.get(k) if d.get(k) != "" else None) for k in keys}


def dec(x):
    return Decimal(str(x if x is not None else 0))


def r4(x):
    """Round to 4 decimals with banker's rounding, using Decimal so binary floats never leak in."""
    return float(dec(x).quantize(Q4, ROUND_HALF_EVEN))


round_q = r4


def proportional(total2, base, q):
    """Customs-unit quantity that goes with `q` commercial units taken from a pool of `base`.

    Taking the whole pool returns the whole customs balance, so partial withdrawals can never
    leave a rounding residue behind.
    """
    if total2 is None:
        return None
    if dec(q) >= dec(base):
        return r4(total2)
    return r4(dec(total2) * dec(q) / dec(base))

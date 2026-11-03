"""Demonstrate deterministic enforcement of the order discount cap."""

from tarka import VerificationStatus, verify


def main() -> None:
    rules = ["applied_discount <= max_discount_percentage"]
    for discount in (30, 20):
        result = verify(
            {"applied_discount": discount, "max_discount_percentage": 20},
            rules=rules,
        )
        print(f"{discount}% discount: {result.status}")
        if result.status is VerificationStatus.UNSAT:
            print(result.message)


if __name__ == "__main__":
    main()
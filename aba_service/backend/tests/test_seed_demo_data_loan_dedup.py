"""seed_demo_data 의 대출 생성 — 같은 책이 동시에 두 번 대출중이 되지 않는지."""

from scripts.seed_demo_data import _would_create_duplicate_active_loan


def test_이미_대출중인_책에_또_대출중을_얹으면_중복으로_판정():
    assert _would_create_duplicate_active_loan(1, True, {1}) is True


def test_다른_책이면_중복_아님():
    assert _would_create_duplicate_active_loan(2, True, {1}) is False


def test_반납완료로_판정되는_대출은_애초에_중복_아님():
    assert _would_create_duplicate_active_loan(1, False, {1}) is False

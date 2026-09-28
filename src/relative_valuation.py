"""상대가치평가(Comparable Company Analysis): PER/PBR/EV-EBIT 멀티플 비교.

주의: 감가상각비를 분리할 수 없어(EV/EBITDA 대신) EV/EBIT를 사용한다.
삼성전자처럼 여러 사업부(반도체/모바일/가전)가 혼재된 복합기업은 단일 업종 피어와
완전히 동일선상에서 비교하기 어렵다는 한계가 있음을 발표 시 명시할 것.
"""
from dataclasses import dataclass


@dataclass
class CompanyMultiples:
    name: str
    price: float
    eps: float | None
    bps: float | None
    per: float | None
    pbr: float | None
    ev_ebit: float | None


def compute_multiples(
    name: str,
    price: float,
    shares: float,
    net_income: float,
    equity: float,
    ebit: float,
    net_debt: float,
) -> CompanyMultiples:
    eps = net_income / shares if shares else None
    bps = equity / shares if shares else None
    per = price / eps if eps and eps > 0 else None
    pbr = price / bps if bps and bps > 0 else None

    market_cap = price * shares
    ev = market_cap + net_debt
    ev_ebit = ev / ebit if ebit and ebit > 0 else None

    return CompanyMultiples(name=name, price=price, eps=eps, bps=bps, per=per, pbr=pbr, ev_ebit=ev_ebit)


def _avg(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def implied_value_from_peers(
    peers: list[CompanyMultiples],
    target_eps: float | None,
    target_bps: float | None,
    target_ebit: float | None,
    target_net_debt: float,
    target_shares: float,
) -> dict:
    per_avg = _avg([p.per for p in peers])
    pbr_avg = _avg([p.pbr for p in peers])
    ev_ebit_avg = _avg([p.ev_ebit for p in peers])

    value_per_share_per = per_avg * target_eps if per_avg and target_eps else None
    value_per_share_pbr = pbr_avg * target_bps if pbr_avg and target_bps else None

    value_per_share_ev_ebit = None
    if ev_ebit_avg and target_ebit:
        implied_ev = ev_ebit_avg * target_ebit
        value_per_share_ev_ebit = (implied_ev - target_net_debt) / target_shares

    return {
        "peer_per_avg": per_avg,
        "peer_pbr_avg": pbr_avg,
        "peer_ev_ebit_avg": ev_ebit_avg,
        "value_per_share_per": value_per_share_per,
        "value_per_share_pbr": value_per_share_pbr,
        "value_per_share_ev_ebit": value_per_share_ev_ebit,
    }

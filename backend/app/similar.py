"""Похожие товары у разных продавцов: та же категория и почти то же название."""
import re
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import Product


def _norm(name: str) -> str:
    return " ".join(re.sub(r"[^a-zа-яё0-9]+", " ", name.lower().replace("ё", "е")).split())


def is_similar(a: Product, b: Product) -> bool:
    """Такой же товар у другого продавца: та же категория, другой продавец, почти одинаковое название."""
    if a.id is not None and a.id == b.id:
        return False
    if (a.category or "other") != (b.category or "other"):
        return False
    if (a.supplier_account_id or 0) == (b.supplier_account_id or 0):
        return False  # у одного продавца это просто разные товары
    na, nb = _norm(a.name), _norm(b.name)
    ta, tb = set(na.split()), set(nb.split())
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return SequenceMatcher(None, na, nb).ratio() >= 0.8 or jaccard >= 0.6


def similar_products(s: Session, prod: Product) -> list[Product]:
    return [p for p in s.scalars(select(Product)) if is_similar(prod, p)]

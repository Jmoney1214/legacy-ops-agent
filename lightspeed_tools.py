import os, asyncio
from typing import Any, Dict, List, Optional
import httpx

# Auto-refresh the token if it's near expiry
try:
    from ls_auth import ensure_access_token  # type: ignore
except Exception:
    ensure_access_token = None  # type: ignore

def _need(name: str) -> str:
    v = os.getenv(name)
    if not v:
        raise RuntimeError(f"Missing env var: {name}")
    return v

def _base_url() -> str:
    return f"https://api.lightspeedapp.com/API/V3/Account/{_need('LS_ACCOUNT_ID')}"

def _auth_headers() -> Dict[str, str]:
    token: Optional[str] = None
    if callable(ensure_access_token):
        try:
            token = ensure_access_token(120)  # refresh if < 2 min left
        except Exception:
            token = None
    if not token:
        token = os.getenv("LS_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("No access token available. Set LS_ACCESS_TOKEN or configure ls_auth.ensure_access_token().")
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}

async def _get_json(url: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    async with httpx.AsyncClient(timeout=30) as cx:
        if os.getenv("LS_DEBUG"):
            print("GET", url, "params", params)
        r = await cx.get(url, params=params, headers=_auth_headers())
        if r.status_code >= 400:
            raise RuntimeError(f"GET {url} -> {r.status_code}: {r.text[:300]}")
        return r.json()

def _as_list(obj):
    if obj is None:
        return []
    return obj if isinstance(obj, list) else [obj]

def _flatten_item_for_shop(item: Dict[str, Any], shop_id: str) -> Optional[Dict[str, Any]]:
    # ItemShops.ItemShop can be object/array/null
    shops = (((item.get("ItemShops") or {}).get("ItemShop")) or [])
    shops = _as_list(shops)
    s = next((x for x in shops if str(x.get("shopID")) == str(shop_id)), None)
    if not s:
        return None
    return {
        "itemID":       item.get("itemID"),
        "systemSku":    item.get("systemSku"),
        "description":  item.get("description"),
        "defaultCost":  item.get("defaultCost"),
        "shopID":       shop_id,
        "price":        s.get("price"),
        "qoh":          s.get("qoh"),
    }

# ---- price-level fallback helpers ----
_shop_price_level_cache: Dict[str, Optional[str]] = {}
async def _shop_price_level(shop_id: str) -> Optional[str]:
    if shop_id in _shop_price_level_cache:
        return _shop_price_level_cache[shop_id]
    url = f"{_base_url()}/Shop/{shop_id}.json"
    data = await _get_json(url)
    shop = data.get("Shop") or data
    pl = shop.get("priceLevelID")
    _shop_price_level_cache[shop_id] = str(pl) if pl is not None else None
    return _shop_price_level_cache[shop_id]

async def _fetch_flatten_one(it: Dict[str, Any], shop: str) -> Optional[Dict[str, Any]]:
    item_id = it.get("itemID")
    if not item_id:
        return None
    url = f"{_base_url()}/Item/{item_id}.json"
    # IMPORTANT: JSON-encoded array for relations
    params = {"load_relations": '["ItemShops"]'}
    async with httpx.AsyncClient(timeout=30) as cx:
        if os.getenv("LS_DEBUG"):
            print("GET", url, "params", params)
        r = await cx.get(url, params=params, headers=_auth_headers())
        if r.status_code >= 400:
            raise RuntimeError(f"GET {url} -> {r.status_code}: {r.text[:300]}")
        one = r.json()
    one = one.get("Item") or one  # normalize single-item shape

    # Primary: ItemShop price+qoh
    row = _flatten_item_for_shop(one, shop)

    # Fallback: effective price from Prices at the shop's price level
    try:
        needs_fallback = (not row) or (row.get("price") in (None, "", 0, "0"))
        if needs_fallback:
            prices = ((one.get("Prices") or {}).get("ItemPrice")) or []
            prices = prices if isinstance(prices, list) else [prices]
            pl = await _shop_price_level(shop)
            chosen = next((p for p in prices if pl and str(p.get("priceLevelID")) == str(pl)), None)
            eff = chosen.get("amount") if chosen else None
            base = {
                "itemID": one.get("itemID"),
                "systemSku": one.get("systemSku"),
                "description": one.get("description"),
                "defaultCost": one.get("defaultCost"),
                "shopID": shop,
                "price": eff,
                "qoh": row.get("qoh") if row else None,
            }
            row = row or base
            if eff is not None:
                row["price"] = eff
    except Exception:
        # Don’t fail the whole call if Prices are missing or shaped oddly
        pass

    return row

async def ls_list_items_flat(limit: int = 25, shop_id: Optional[str] = None) -> Dict[str, Any]:
    """
    Robust path:
      1) List Items without relations (avoids 400 on some tenants)
      2) For each item, GET /Item/<id>.json?load_relations=["ItemShops","Prices"]
      3) Flatten to {itemID, systemSku, description, defaultCost, shopID, price, qoh}
    Returns: {"count": n, "items": [...]}
    """
    shop = shop_id or _need("LS_SHOP_ID")
    list_url = f"{_base_url()}/Item.json"

    want = max(1, min(limit, 1000))
    page_size = min(100, want)

    # First page WITHOUT load_relations and WITHOUT offset (cursor-based)
    params = {"limit": page_size, "archived": 0}
    data = await _get_json(list_url, params=params)

    def _items_from(resp: Dict[str, Any]) -> List[Dict[str, Any]]:
        it = resp.get("Item") or []
        return it if isinstance(it, list) else [it]

    def _next_href(resp: Dict[str, Any]) -> Optional[str]:
        attrs = resp.get("@attributes") or {}
        nxt = attrs.get("next")
        return nxt if nxt else None

    items = _items_from(data)
    out: List[Dict[str, Any]] = []

    # Flatten first page
    for it in items:
        row = await _fetch_flatten_one(it, shop)
        if row:
            out.append(row)
            if len(out) >= want:
                return {"count": len(out), "items": out}

    # Follow cursor if more needed
    next_url = _next_href(data)
    while next_url and len(out) < want:
        data = await _get_json(next_url, params=None)  # next contains full query
        for it in _items_from(data):
            row = await _fetch_flatten_one(it, shop)
            if row:
                out.append(row)
                if len(out) >= want:
                    break
        if len(out) >= want:
            break
        next_url = _next_href(data)

    return {"count": len(out), "items": out}

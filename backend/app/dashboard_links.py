from urllib.parse import quote


def dashboard_public_base_url(
    public_url: str | None = None,
    request_base_url: str | None = None,
) -> str:
    base_url = (public_url or "").strip() or (request_base_url or "").strip()
    return base_url.rstrip("/")


def build_dashboard_view_url(
    user_id: int,
    public_url: str | None = None,
    request_base_url: str | None = None,
) -> str:
    base_url = dashboard_public_base_url(public_url, request_base_url)
    path = f"/api/v1/dashboard/view/{user_id}"
    if not base_url:
        return path
    return f"{base_url}{path}"


def build_household_dashboard_view_url(
    household_id: str = "default",
    public_url: str | None = None,
    request_base_url: str | None = None,
) -> str:
    base_url = dashboard_public_base_url(public_url, request_base_url)
    safe_household_id = (household_id or "default").strip() or "default"
    path = f"/api/v1/dashboard/household/{quote(safe_household_id, safe='')}/view"
    if not base_url:
        return path
    return f"{base_url}{path}"

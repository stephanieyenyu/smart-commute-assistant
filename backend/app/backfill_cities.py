import asyncio

from app.address_utils import extract_city_from_text, normalize_city_name
from app.db import SessionLocal
from app.models import CommuteProfile
from app.google_maps import geocode_address


def _pick_city(result: dict | None, fallback_address: str | None = None) -> str | None:
    if not result:
        return extract_city_from_text(fallback_address)

    city = (
        result.get("city")
        or result.get("county")
        or result.get("administrative_area")
    )
    normalized = normalize_city_name(city)
    return normalized or extract_city_from_text(fallback_address)


def _pick_township(result: dict | None) -> str | None:
    if not result:
        return None
    return (
        result.get("township")
        or result.get("district")
        or result.get("sublocality")
    )


async def main():
    db = SessionLocal()
    try:
        profiles = db.query(CommuteProfile).all()

        for profile in profiles:
            updated = False

            if profile.home_address and not profile.home_city:
                result = await geocode_address(profile.home_address)
                picked_city = _pick_city(result, profile.home_address)
                picked_township = _pick_township(result)
                if picked_city:
                    profile.home_city = picked_city
                    print(f"[backfill] home_city set to {profile.home_city} for profile {profile.id}")
                    updated = True
                else:
                    print(f"[backfill] failed to get home_city for profile {profile.id}")
                if picked_township and not profile.home_township:
                    profile.home_township = picked_township
                    print(f"[backfill] home_township set to {profile.home_township} for profile {profile.id}")
                    updated = True

            if profile.office_address and not profile.office_city:
                result = await geocode_address(profile.office_address)
                picked_city = _pick_city(result, profile.office_address)
                picked_township = _pick_township(result)
                if picked_city:
                    profile.office_city = picked_city
                    print(f"[backfill] office_city set to {profile.office_city} for profile {profile.id}")
                    updated = True
                else:
                    print(f"[backfill] failed to get office_city for profile {profile.id}")
                if picked_township and not profile.office_township:
                    profile.office_township = picked_township
                    print(f"[backfill] office_township set to {profile.office_township} for profile {profile.id}")
                    updated = True

            if updated:
                db.add(profile)

        db.commit()
        print("[backfill] done")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())

import asyncio

from app.db import SessionLocal
from app.models import CommuteProfile
from app.google_maps import geocode_address


async def main():
    db = SessionLocal()
    try:
        profiles = db.query(CommuteProfile).all()

        for profile in profiles:
            updated = False

            if profile.home_address and not profile.home_city:
                result = await geocode_address(profile.home_address)
                if result and result.get("city"):
                    profile.home_city = result["city"]
                    print(f"[backfill] home_city set to {profile.home_city} for profile {profile.id}")
                    updated = True
                else:
                    print(f"[backfill] failed to get home_city for profile {profile.id}")

            if profile.office_address and not profile.office_city:
                result = await geocode_address(profile.office_address)
                if result and result.get("city"):
                    profile.office_city = result["city"]
                    print(f"[backfill] office_city set to {profile.office_city} for profile {profile.id}")
                    updated = True
                else:
                    print(f"[backfill] failed to get office_city for profile {profile.id}")

            if updated:
                db.add(profile)

        db.commit()
        print("[backfill] done")

    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
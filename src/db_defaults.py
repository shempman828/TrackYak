from src.db_tables import Place, Role
from src.logger_config import logger


class Defaults:
    def __init__(self, session_factory):
        self.Session = session_factory

    def insert_defaults(self):
        session = self.Session()
        try:
            # -----------------
            # Roles
            # -----------------
            role_count = session.query(Role).count()
            if role_count == 0:
                album_artist_role = Role(
                    role_name="Album Artist",
                    role_type="credits",
                    role_description="Primary artist(s) for the album",
                )
                session.add(album_artist_role)
                session.commit()
                logger.info("Inserted default role: Album Artist")
            else:
                logger.debug("Roles already exist, skipping role defaults.")

            # -----------------
            # Places (Motown example)
            # -----------------
            place_count = session.query(Place).count()
            if place_count == 0:
                usa = Place(
                    place_name="USA",
                    place_type="Country",
                    place_latitude=40.98702074333773,
                    place_longitude=-102.49637530789931,
                    place_description=(
                        "The United States of America (USA) is a federal republic of "
                        "50 states located primarily in North America."
                    ),
                )
                session.add(usa)
                session.flush()  # get usa.id

                michigan = Place(
                    place_name="Michigan",
                    place_type="State",
                    place_latitude=43.19487499728767,
                    place_longitude=-84.61365223278996,
                    place_description=(
                        "Michigan is a peninsular state in the Great Lakes region of the "
                        "Upper Midwestern United States."
                    ),
                    parent_id=usa.place_id,
                )
                session.add(michigan)
                session.flush()

                wayne_county = Place(
                    place_name="Wayne County",
                    place_type="County",
                    place_latitude=42.28738988216699,
                    place_longitude=-83.2538597007967,
                    place_description=(
                        "Wayne County is the most populous county in Michigan. "
                        "Its county seat is Detroit."
                    ),
                    parent_id=michigan.place_id,
                )
                session.add(wayne_county)
                session.flush()

                detroit = Place(
                    place_name="Detroit",
                    place_type="City",
                    place_latitude=42.329726421485255,
                    place_longitude=-83.04178265783061,
                    place_description=(
                        "Detroit is Michigan’s largest city and a major cultural center, "
                        "particularly influential in American music history."
                    ),
                    parent_id=wayne_county.place_id,
                )
                session.add(detroit)
                session.flush()

                hitsville = Place(
                    place_name="Hitsville U.S.A.",
                    place_type="Building",
                    place_latitude=42.36419992059792,
                    place_longitude=-83.08848470661147,
                    place_description=(
                        '"Hitsville U.S.A." was Motown’s first headquarters and recording '
                        "studio, purchased by Berry Gordy Jr. in 1959. Today it operates "
                        "as the Motown Museum."
                    ),
                    parent_id=detroit.place_id,
                )
                session.add(hitsville)
                session.flush()

                snakepit = Place(
                    place_name='Motown Studio A ("The Snakepit")',
                    place_type="Room",
                    place_latitude=42.36419992059792,
                    place_longitude=-83.08848470661147,
                    place_description=(
                        'Motown Studio A, nicknamed "the Snakepit," was the low-ceilinged '
                        "basement recording room at Hitsville U.S.A. where much of the "
                        "classic Motown Sound was created."
                    ),
                    parent_id=hitsville.place_id,
                )
                session.add(snakepit)

                session.commit()
                logger.info(
                    "Inserted default place hierarchy: Hitsville U.S.A. / Snakepit"
                )

            else:
                logger.debug("Places already exist, skipping place defaults.")

        except Exception as e:
            session.rollback()
            logger.error(f"Error inserting default data: {e}")

        finally:
            session.close()

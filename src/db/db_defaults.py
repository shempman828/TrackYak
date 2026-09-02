from sqlalchemy.exc import SQLAlchemyError

from src.db.db_tables import ArtistType, Chart, Place, PlaceAssociationType, Role
from src.foundation.logger_config import logger


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
            # Artist types
            # -----------------
            artist_type_count = session.query(ArtistType).count()
            if artist_type_count == 0:
                session.add_all(
                    [
                        ArtistType(type_name=name)
                        for name in (
                            "Composer",
                            "Producer",
                            "Songwriter",
                            "Vocalist",
                            "Instrumentalist",
                            "Multi-Instrumentalist",
                            "Conductor",
                            "Arranger",
                            "DJ",
                            "Engineer",
                        )
                    ]
                )
                session.commit()
                logger.info("Inserted default artist types.")
            else:
                logger.debug("Artist types already exist, skipping defaults.")

            # -----------------
            # Place association types
            # -----------------
            assoc_type_count = session.query(PlaceAssociationType).count()
            if assoc_type_count == 0:
                session.add_all(
                    [
                        PlaceAssociationType(
                            type_name="Birthplace", type_description="Where a person was born"
                        ),
                        PlaceAssociationType(
                            type_name="Origin",
                            type_description="Where a band/artist formed or originated",
                        ),
                        PlaceAssociationType(
                            type_name="Hometown", type_description="Where a person grew up"
                        ),
                        PlaceAssociationType(
                            type_name="Residence", type_description="Where a person lived/lives"
                        ),
                        PlaceAssociationType(
                            type_name="Deathplace", type_description="Where a person died"
                        ),
                        PlaceAssociationType(
                            type_name="Recording Location",
                            type_description="Where a track/album was recorded",
                        ),
                        PlaceAssociationType(
                            type_name="Release Location",
                            type_description="Where an album was released",
                        ),
                        PlaceAssociationType(
                            type_name="Song About",
                            type_description="Place is the subject/theme of a track",
                        ),
                        PlaceAssociationType(
                            type_name="Headquartered In",
                            type_description="Where a publisher/label is based",
                        ),
                        PlaceAssociationType(
                            type_name="Associated",
                            type_description="Generic/unspecified place association",
                        ),
                    ]
                )
                session.commit()
                logger.info("Inserted default place association types.")
            else:
                logger.debug("Place association types already exist, skipping defaults.")

            # -----------------
            # Places (MBID-backed example chains: USA, Japan, England)
            #
            # Each chain is real MusicBrainz data (area/place MBIDs, names,
            # and area "type" values taken verbatim from the MusicBrainz API,
            # matching what src/musicbrainz/musicbrainz_client.py's
            # resolve_area_chain() + place_chain_resolver.py would produce
            # for a genuine import), so these rows look and behave exactly
            # like real imported places.
            # -----------------
            place_count = session.query(Place).count()
            if place_count == 0:
                # --- USA: Sun Studio, Memphis, Tennessee ---
                united_states = Place(
                    place_name="United States",
                    place_type="Country",
                    place_latitude=40.98702074333773,
                    place_longitude=-102.49637530789931,
                    place_description=(
                        "The United States of America (USA) is a federal republic of "
                        "50 states located primarily in North America."
                    ),
                    MBID="489ce91b-6658-3307-9877-795b68554c98",
                )
                session.add(united_states)
                session.flush()

                tennessee = Place(
                    place_name="Tennessee",
                    place_type="Subdivision",
                    place_latitude=35.7478,
                    place_longitude=-86.6923,
                    place_description=(
                        "Tennessee is a US state in the Southeastern United States, home "
                        "to Memphis and Nashville and a birthplace of blues, country, "
                        "soul, and rock and roll."
                    ),
                    parent_id=united_states.place_id,
                    MBID="f9caf2d8-9638-4b96-bc49-8462339d4b2e",
                )
                session.add(tennessee)
                session.flush()

                shelby_county = Place(
                    place_name="Shelby County",
                    place_type="County",
                    place_latitude=35.1622,
                    place_longitude=-89.9006,
                    place_description=(
                        "Shelby County is the most populous county in Tennessee. "
                        "Its county seat is Memphis."
                    ),
                    parent_id=tennessee.place_id,
                    MBID="9f1c845b-0536-4c8e-b40e-6b4dcd3eaac6",
                )
                session.add(shelby_county)
                session.flush()

                memphis = Place(
                    place_name="Memphis",
                    place_type="City",
                    place_latitude=35.1495,
                    place_longitude=-90.0490,
                    place_description=(
                        "Memphis is a city on the Mississippi River in Tennessee, a "
                        "birthplace of blues and rock and roll and home to Sun Studio "
                        "and Stax Records."
                    ),
                    parent_id=shelby_county.place_id,
                    MBID="c2d96f61-75a4-4375-aed5-6aacb0b6326a",
                )
                session.add(memphis)
                session.flush()

                sun_studio = Place(
                    place_name="Sun Studio",
                    place_type="Studio",
                    place_latitude=35.139247,
                    place_longitude=-90.037678,
                    place_description=(
                        "Sun Studio in Memphis, opened by Sam Phillips in 1950, is where "
                        "Elvis Presley, Johnny Cash, B.B. King, and Jerry Lee Lewis all "
                        "made some of their earliest recordings."
                    ),
                    parent_id=memphis.place_id,
                    MBID="87e298cb-d108-4edf-ba51-5b96bdab33de",
                )
                session.add(sun_studio)
                session.flush()

                # --- Japan: Nippon Budokan, Chiyoda, Tokyo ---
                japan = Place(
                    place_name="Japan",
                    place_type="Country",
                    place_latitude=36.2048,
                    place_longitude=138.2529,
                    place_description=(
                        "Japan is an island country in East Asia, with Tokyo as its "
                        "capital and largest city."
                    ),
                    MBID="2db42837-c832-3c27-b4a3-08198f75693c",
                )
                session.add(japan)
                session.flush()

                tokyo = Place(
                    place_name="Tokyo",
                    place_type="Subdivision",
                    place_latitude=35.6762,
                    place_longitude=139.6503,
                    place_description=(
                        "Tokyo is a subdivision (metropolis) of Japan and the country's capital."
                    ),
                    parent_id=japan.place_id,
                    MBID="8dc97297-ac95-4d33-82bc-e07fab26fb5f",
                )
                session.add(tokyo)
                session.flush()

                chiyoda = Place(
                    place_name="Chiyoda",
                    place_type="City",
                    place_latitude=35.6938,
                    place_longitude=139.7530,
                    place_description=(
                        "Chiyoda is a special ward of Tokyo, home to the Imperial Palace "
                        "and Kitanomaru Park."
                    ),
                    parent_id=tokyo.place_id,
                    MBID="f5cd9916-2a4e-4abf-a301-9525ef598436",
                )
                session.add(chiyoda)
                session.flush()

                kitanomaru_koen = Place(
                    place_name="Kitanomaru Kōen",
                    place_type="District",
                    place_latitude=35.6935,
                    place_longitude=139.7530,
                    place_description=(
                        "Kitanomaru Kōen is a public park district in Chiyoda, Tokyo, "
                        "and home to the Nippon Budokan arena."
                    ),
                    parent_id=chiyoda.place_id,
                    MBID="e24c0f02-9b5a-4f4f-9fe0-f8b3e67874f8",
                )
                session.add(kitanomaru_koen)
                session.flush()

                nippon_budokan = Place(
                    place_name="Nippon Budokan",
                    place_type="Indoor arena",
                    place_latitude=35.69333,
                    place_longitude=139.75,
                    place_description=(
                        "Nippon Budokan is an indoor arena in Tokyo's Kitanomaru Park, "
                        "opened in 1964, famed in music history as the venue behind "
                        "numerous live albums, including Cheap Trick at Budokan and Bob "
                        "Dylan at Budokan."
                    ),
                    parent_id=kitanomaru_koen.place_id,
                    MBID="4d43b9d8-162d-4ac5-8068-dfb009722484",
                )
                session.add(nippon_budokan)
                session.flush()

                # --- England: Abbey Road Studios, St John's Wood, London ---
                united_kingdom = Place(
                    place_name="United Kingdom",
                    place_type="Country",
                    place_latitude=54.7023545,
                    place_longitude=-3.2765753,
                    place_description=(
                        "The United Kingdom of Great Britain and Northern Ireland is a "
                        "sovereign country in northwestern Europe."
                    ),
                    MBID="8a754a16-0027-3a29-b6d7-2b40ea0481ed",
                )
                session.add(united_kingdom)
                session.flush()

                england = Place(
                    place_name="England",
                    place_type="Subdivision",
                    place_latitude=52.5310214,
                    place_longitude=-1.2649062,
                    place_description=(
                        "England is a country that is part of the United Kingdom, "
                        "occupying the majority of the island of Great Britain."
                    ),
                    parent_id=united_kingdom.place_id,
                    MBID="9d5dd675-3cf4-4296-9e39-67865ebee758",
                )
                session.add(england)
                session.flush()

                london = Place(
                    place_name="London",
                    place_type="City",
                    place_latitude=51.5156177,
                    place_longitude=-0.0919983,
                    place_description=(
                        "London is the capital and largest city of England and the United Kingdom."
                    ),
                    parent_id=england.place_id,
                    MBID="f03d09b3-39dc-4083-afd6-159e3f0d462f",
                )
                session.add(london)
                session.flush()

                westminster = Place(
                    place_name="Westminster",
                    place_type="Subdivision",
                    place_latitude=51.4973206,
                    place_longitude=-0.137149,
                    place_description=(
                        "The City of Westminster is a London borough that includes St "
                        "John's Wood, among many other districts."
                    ),
                    parent_id=london.place_id,
                    MBID="48d08ee1-db45-4566-bb1d-c47ab6dbaf98",
                )
                session.add(westminster)
                session.flush()

                st_johns_wood = Place(
                    place_name="St John's Wood",
                    place_type="District",
                    place_latitude=51.531726,
                    place_longitude=-0.1741901,
                    place_description=(
                        "St John's Wood is an affluent district in the City of "
                        "Westminster, London, best known as the home of Abbey Road "
                        "Studios."
                    ),
                    parent_id=westminster.place_id,
                    MBID="57296e85-ed07-4d79-88ef-7b70d23acf8d",
                )
                session.add(st_johns_wood)
                session.flush()

                abbey_road_studios = Place(
                    place_name="Abbey Road Studios",
                    place_type="Studio",
                    place_latitude=51.53192,
                    place_longitude=-0.17835,
                    place_description=(
                        "Abbey Road Studios in St John's Wood, London, opened by EMI in "
                        "1931, is renowned as the recording home of the Beatles, whose "
                        "1969 album Abbey Road takes its name from the studio's address."
                    ),
                    parent_id=st_johns_wood.place_id,
                    MBID="bd55aeb7-19d1-4607-a500-14b8479d3fed",
                )
                session.add(abbey_road_studios)

                session.commit()
                logger.info(
                    "Inserted default MBID-backed place chains: Sun Studio (USA), "
                    "Nippon Budokan (Japan), Abbey Road Studios (England)"
                )

            else:
                logger.debug("Places already exist, skipping place defaults.")

            # -----------------
            # Charts
            # -----------------
            from src.charts.chart_download import CHART_FILES

            chart_count = session.query(Chart).count()
            if chart_count == 0:
                session.add_all(
                    [
                        Chart(
                            chart_key="hot-100",
                            chart_name="Billboard Hot 100",
                            source_url=CHART_FILES["hot-100"][1],
                            matched_entity_type="Track",
                        ),
                        Chart(
                            chart_key="billboard-200",
                            chart_name="Billboard 200",
                            source_url=CHART_FILES["billboard-200"][1],
                            matched_entity_type="Album",
                        ),
                    ]
                )
                session.commit()
                logger.info("Inserted default chart registry rows: Hot 100, Billboard 200")
            else:
                logger.debug("Charts already exist, skipping chart defaults.")

        except SQLAlchemyError as e:
            session.rollback()
            logger.error(f"Error inserting default data: {e}")

        finally:
            session.close()

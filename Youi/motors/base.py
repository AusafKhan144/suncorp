from datetime import date, datetime
from typing import Any, Optional
import asyncio
import json
from datetime import datetime, date
import copy
from datetime import date, datetime
import json
import os
import random
import time
import pandas as pd
import requests
import asyncio
import re
import logging
from fuzzywuzzy import fuzz
from fuzzywuzzy import process
from requests.exceptions import SSLError, ReadTimeout, ProxyError
import csv
import os
import random
import time
from typing import Any, Dict, Optional

import requests
from requests.exceptions import RequestException, SSLError
import logging
from utilities import get_headers_playwright

logger = logging.getLogger(__name__)


class YouiMappings:

    YES_NO_DICT = {"No": "808", "Yes": "807"}
    GENDERS = {"M": "085", "F": "086"}
    CAR_COLOR_DICT = {
        "Beige": "516",
        "Black": "509",
        "Blue": "510",
        "Brown": "521",
        "Burgundy": "523",
        "Charcoal": "525",
        "Gold": "522",
        "Green": "511",
        "Grey": "526",
        "Maroon": "520",
        "Orange": "512",
        "Pink": "524",
        "Purple": "513",
        "Red": "514",
        "Silver": "519",
        "Champagne": "10661",
        "White": "515",
        "Yellow": "518",
    }
    
    VEHICLE_USAGE_PURPOSE = {"Private": "12439", "Business": "10384"}
    TRIPS_TO_WORK = {
        "1": "10724",
        "2": "10725",
        "3": "10726",
        "4": "10727",
        "5": "10728",
        "6": "10729",
        "7": "10730",
    }
    STORAGE_TYPE = {
        "Garage - Private And Locked": "6494",
        "Garage - Public Parking": "6501",
        "Garage - Shared But Access Controlled": "6502",
        "Outside - Behind A Locked Gate": "6497",
        "Outside - On The Property": "6495",
        "Outside - Other": "6496",
        "Under A Carport": "10825",
    }
    DRIVING_DEMERITS = {
        "0": "10539",
        "Unsure": "10804",
        "1 To 3 Points": "10792",
        "4 To 6 Points": "10795",
        "7 To 12 Points": "10798",
        "More Than 12 Points": "10775",
    }
    MOBILE_PHONE_USAGE = {
        "Never": "12395",
        "Without A Handsfree Kit - Up To 2 Times Per Week": "12396",
        "Without A Handsfree Kit - More Than 2 Times Per Week": "12397",
        "With A Handsfree Kit - Up To 2 Times Per Week": "12398",
        "With A Handsfree Kit - More Than 2 Times Per Week": "12399",
    }
    MONTH_NAME_TO_NUM = {
        "January": "1",
        "February": "2",
        "March": "3",
        "April": "4",
        "May": "5",
        "June": "6",
        "July": "7",
        "August": "8",
        "September": "9",
        "October": "10",
        "November": "11",
        "December": "12",
    }
    INCIDENT_TYPES = {
        "Not At Fault Collision": "10823",
        "Accident Or Any Other Damage": "5541",
        "Glass/windscreen Only": "5547",
        "Loss, Theft Or Attempted Theft": "5562",
        "Natural Hazard ( Hail, Storm, Bushfire Etc.)": "5591",
    }
    EMPLOYMENT_TYPE = {
        "Employed": "10353",
        "Self Employed": "10361",
        "Full Time Student": "10354",
        "Home Duties": "10378",
        "Retired": "10362",
        "Unemployed": "10355",
    }
    OCCUPATION_TYPE = {
        "The Arts": "0001",
        "Clerical/Administrative": "0002",
        "Retail Sales": "0003",
        "Driver/Courier": "0004",
        "Education": "0005",
        "Farming/Agricultural": "0006",
        "Manual, e.g. Tradie": "0008",
        "Manual, e.g. Cafe/Bar Staff": "0009",
        "Medical": "0010",
        "Professional, e.g. Accountant/Engineer": "0011",
        "Public Service": "0012",
        "Non-Retail Sales": "0013",
        "Travel Industry": "0016",
    }

    INSURANCE_PROVIDER ={
            'Aami': '056',
            'Allianz': '060',
            'Gio': '061',
            'Nrma': '057',
            'Qbe': '086',
            'Raa': '089',
            'Racq': '079',
            'Rac': '088',
            'Ract': '080',
            'Racv': '081',
            'Shannons': '082',
            'Suncorp': '083',
            'Youi':'087',
        }
    COVER_TYPE_MAPPINGS= {
        "Comprehensive": "COMP",
        "Third Party Fire And Theft": "LTDC",
        "Third Party Property Damage": "OTHER",
    }
    COVER_DURATION = {'0 Up To 3 Months': '7723', 'From 3 Up To 6 Months': '7724', 'From 6 Up To 12 Months': '7725', 'From 1 Year Up To 2 Years': '7726', 'From 2 Up To 3 Years': '7727', 'From 3 Up To 5 Years': '7728', 'From 5 Up To 10 Years': '7729', '10 Years And Over': '7730'}
    NO_COVER_DURATION = {'0 Up To 1 Month': '6827', 'From 1 Up To 6 Months': '6828', '6 Months And Over': '6829', 'Never Been Covered': '6830'}
    TRAVEL_MAPPING = {'0-5,000km': '14473','5,001-10,000km': '14474','10,001-15,000km': '14475','15,001-20,000km': '14476','20,001-30,000km': '14477','30,001+km': '14478'}
    PREVIOUS_INSURANCE_COVER = {'Comprehensive': '5177','Cover Note': '6712','Third Party Fire & Theft': '5178','Third Party Property Only': '5179','Not Insured': '5176','Do Not Know': '10376'}
    NEW_OR_USED_VEHICLE = {'New': '5290', 'Demo': '5291', 'Used - Already Owner': '5292', 'Used - Buying From Dealer': '10373', 'Used - Buying On Auction': '10374', 'Used - Buying From Private Seller': '10375'}
    LICENSE_MAPPING = {'Australian Open Licence': '10246','Provisional Licence': '10304','Learners Permit': '10210','International Licence': '10247','No Licence': '9967'}

    def __init__(self, input_data: dict[str, Any]) -> None:
        """
        Initialize with user input data.
        """
        self.input_data = {k: v.title() if isinstance(v, str) else str(v).replace('.0','') for k, v in input_data.items() if v != ''}
        self.cover_type: Optional[str] = None
        self.policy_holder: dict[str, Any] = {}

        # === Utility Methods ===

    def _map_value(
        self, value: Any, mapping: dict, default: Any = None, log_field: str = ""
    ) -> Any:
        """
        Utility to safely map a value using a dictionary, with logging if not found.
        """
        normalized_mapping = {k.title():v for k,v in mapping.items()}
        result = normalized_mapping.get(value, default)
        if result is None and value != "":
            logger.warning(f"Value '{value}' not found in mapping '{log_field}'.")
        return result

    def _safe_int(self, value: Any, default: int = 0) -> int:
        """
        Convert value to int, return default if conversion fails.
        """
        try:
            return int(value)
        except (ValueError, TypeError):
            return default

    # === Translation Methods (with docstrings, comments, and improved error handling) ===

    def translate_input_data_page_01(self, progress_dict) -> dict[str, Any]:

        translation = {
            "vehicleSearchBy": "VehicleSelector",
            "vehicleMakeCode": progress_dict["car_make_code"],
            "vehicleMakeName": progress_dict["car_make_name"],
            "vehicleYear": str(progress_dict["car_make_year"]),
            "vehicleModel": progress_dict["car_model_key"],
            "vehicleBadge": progress_dict["car_badge"],
            "vehicleStyle": progress_dict["car_style"],
            "vehicleModelCode": str(progress_dict["car_description"]),
            "vehicleColourCode": self._map_value(
                self.input_data.get("COLOUR"), self.CAR_COLOR_DICT, log_field="COLOUR"
            ),
        }

        return translation

    def translate_input_data_page_02(
        self,
    ) -> dict:

        policy_start_date_day = self._safe_int(self.input_data.get("POLICY_START_DAY"))
        policy_start_date_month = self._safe_int(
            self.input_data.get("POLICY_START_MONTH")
        )
        policy_start_date_year = self._safe_int(
            self.input_data.get("POLICY_START_YEAR")
        )

        dt = datetime(
            policy_start_date_year, policy_start_date_month, policy_start_date_day
        )
        policy_start_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        translation = {
            "coverType": self._map_value(
                self.input_data.get("COVER_TYPE"),
                self.COVER_TYPE_MAPPINGS,
                log_field="COVER_TYPE",
            ),
            "policyStartDate": policy_start_date,
        }

        return translation

    def translate_input_date_page_03(
        self,business_usage
    ) -> dict:

        usage_purpose = self._map_value(
            self.input_data.get("PG_VEHICLEUSE1"),
            self.VEHICLE_USAGE_PURPOSE,
            log_field="PG_VEHICLEUSE1",
        )
        trips_to_work_or_Study = self._map_value(
            self.input_data.get("DAYSPERWEEK"),
            self.TRIPS_TO_WORK,
            log_field="DAYSPERWEEK",
        )

        if usage_purpose == self.VEHICLE_USAGE_PURPOSE.get("Private"):
            translation = {
                "vehicleUsageJourneyWorkOrStudy": "658",  # always yes
                "vehicleUsageNumberOfTripsToWorkOrStudy": trips_to_work_or_Study,
                "vehicleUsagePrivateOrBusiness": usage_purpose,
                "isVehicleUsedForTowing": False,
                "riskTypesTowed": [],
            }
        else:
            
            translation = {
                "vehicleUsagePrivateOrBusiness": usage_purpose,
                "radiusOfOperations": "10746",
                "businessActivity": business_usage,
            }

        return translation

    def translate_input_date_page_04(self, property_address):

        if property_address:
            suburb_object = copy.deepcopy(property_address.get("suburb"))
            if suburb_object:
                del suburb_object['earthquakeVulnerable']

        translation = {
            "storedDaytimeParkingDescription": self._map_value(
                self.input_data.get("PG_GARAGE"),
                self.STORAGE_TYPE,
                log_field="PG_GARAGE",
            ),
            "storedOvernightLocationDescription": self._map_value(
                self.input_data.get("PG_GARAGE"),
                self.STORAGE_TYPE,
                log_field="PG_GARAGE",
            ),
            "storedOvernightAddress": property_address,
            "storedDaytimeSuburb": suburb_object,
            "salesConsent": {
            "contactId": None,
            "isVehicleUsedForTowing": False,
            "riskTypesTowed": [
            ],
            "salesConsentDetails": [
                {
                    "hasConsented": False,
                    "riskType": 9
                }
            ]
        }
        }

        return translation

    def translate_input_data_page_05(self):

        first_name = self.input_data.get("FIRST_NAME_DUMMY")
        date_of_birth_day = self._safe_int(self.input_data.get("DOB_DUMMY_DAY"))
        date_of_birth_month = self._safe_int(self.input_data.get("DOB_DUMMY_MONTH"))
        date_of_birth_year = self._safe_int(self.input_data.get("DOB_DUMMY_YEAR"))

        dt = datetime(date_of_birth_year, date_of_birth_month, date_of_birth_day)
        date_of_birth = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        translation = {
            "people": [
                {
                    "birthDate": date_of_birth,
                    "firstName": first_name,
                    "gender": self._map_value(
                        self.input_data.get("PG_GENDER"),
                        self.GENDERS,
                        log_field="PG_GENDER",
                    ),
                    "licenceDetails": {},
                    "personKey": "NEW",
                    "personTypes": ["POLICY HOLDER"],
                    "previousIncidents": [],
                }
            ]
        }

        return translation

    def translate_input_data_page_06(self):

        translation = {
            "licenceDetails": {
                "anyDemeritPoints": self._map_value(
                    "0", self.DRIVING_DEMERITS, log_field="DRIVING_DEMERITS"
                ),
                "licenceType": self._map_value(
                    self.input_data.get("LICENCE_TYPE"),
                    self.LICENSE_MAPPING,
                    log_field="LICENCE_TYPE",
                ),
            },
            "useMobilePhone": self._map_value(
                "Never", self.MOBILE_PHONE_USAGE, log_field="MOBILE_PHONE_USAGE"
            ),
        }
        return translation

    def translate_input_data_page_07(self):

        incident_year = self._safe_int(
            self.input_data.get("DATE_INCIDENT_YEAR", "2000")
        )
        incident_month = self._safe_int(
            self._map_value(
                self.input_data.get("DATE_INCIDENT_MONTH"),
                self.MONTH_NAME_TO_NUM,
                default=self.input_data.get("DATE_INCIDENT_MONTH"),
                log_field="DATE_INCIDENT_MONTH",
            )
        )
        incident_description = self._map_value(
            self.input_data.get("INCIDENT_CAUSE1", "").title(),
            self.INCIDENT_TYPES,
            default=None,
            log_field="INCIDENT_CAUSE1",
        )

        if incident_description:
            translation = {
                "hasPreviousIncidents": self.YES_NO_DICT.get("Yes"),
                "previousIncidents":[ {
                    "year": incident_year,
                    "month": incident_month,
                    "description": incident_description,
                }],
            }
        else:
            translation = {"hasPreviousIncidents": self.YES_NO_DICT.get("No")}

        return translation

    def translate_input_data_page_08(self):

        employment_type_key = self._map_value(
            self.input_data.get("EMPLOYMENT"),
            self.EMPLOYMENT_TYPE,
            log_field="EMPLOYMENT",
        )

        occupation = self._map_value(
            self.input_data.get("OCCUPATION"),
            self.OCCUPATION_TYPE,
            log_field="OCCUPATION",
        )

        if occupation:
            translation = {
                "employmentStatus": employment_type_key,
                "occupationIndustry": occupation,
            }
        else:
            translation = {"employmentStatus": employment_type_key}

        return translation

    def translate_input_data_page_09(self):

        held_insurance_yes_no = self._map_value(
            self.input_data.get("HELD_INS_2W"),
            self.YES_NO_DICT,
            log_field="HELD_INS_2W",
        )

        if held_insurance_yes_no == self.YES_NO_DICT.get('Yes'):
            translation = {
                "insuranceHistory": {
                    "currentlyCovered": held_insurance_yes_no,
                    
                    "coverDuration": self._map_value(self.input_data.get("PC_YRSINSURED"),self.COVER_DURATION,log_field="PC_YRSINSURED"),
                    "insuranceCompanyCode": self._map_value(self.input_data.get("PG_PREVIOUSINSURER"),self.INSURANCE_PROVIDER,log_field="PG_PREVIOUSINSURER"),
                }
            }
        else:
            translation = {
                "insuranceHistory": {
                    "currentlyCovered": held_insurance_yes_no,
                    "noCoverDuration": self._map_value('Never Been Covered',self.NO_COVER_DURATION,log_field="PC_YRSINSURED"),
                }
            }

        return translation
    
    def translation_input_data_page_10(self):
            
        travel_input_key = self._map_value(self.input_data.get("PG_ANNUALKM"),self.TRAVEL_MAPPING,log_field="PG_ANNUALKM")
        previous_insurance_key = self._map_value(self.input_data.get("PREVIOUSINSURER_COVER"),self.PREVIOUS_INSURANCE_COVER,log_field="PREVIOUSINSURER_COVER")
        new_or_used_vehicle = self._map_value("Used - Already Owner",self.NEW_OR_USED_VEHICLE,log_field="NEW_OR_USED_VEHICLE")

        translation = {
            "newOrUsedVehicle": new_or_used_vehicle,  # always user-already owned
            "insuranceCover": previous_insurance_key,
            "distanceTravelled": travel_input_key,
            "hasAfterMarketAccessories": self.YES_NO_DICT.get("No"),
            "vehicleHasExistingDamage": self.YES_NO_DICT.get("No"),
            "vehicleHasHailDamage": self.YES_NO_DICT.get("No"),
            "vehicleHasModifications": self.YES_NO_DICT.get("No"),
            "vehicleHasOptFfAccessories": self.YES_NO_DICT.get("No"),
            "vehicleHasOtherDamage": self.YES_NO_DICT.get("No"),
            "vehicleHasWindscreenDamage": self.YES_NO_DICT.get("No"),
            "policyholderPossession": self.YES_NO_DICT.get("Yes"),
        }

        return translation


    def translation_input_data_page_11(self,property_address):
        surname = self.input_data.get("LAST_NAME_DUMMY")
        age = self._safe_int(self.input_data.get("PC_AGE"))
        license_held_for_years = self._safe_int(self.input_data.get("PC_YEARSHELDLICENSE"))

        license_age = age - license_held_for_years

        translation = {
            "licenceDetails" : {
                "hasOtherInfringements": self.YES_NO_DICT.get("No"),
                "licenceAge": license_age,
            },
            "people":{
                "accessToAnotherVehicle": self.YES_NO_DICT.get("No"),
                "driverType": "LISTED DRIVER",
                "personTypes": ["POLICY HOLDER", "MANDATE", "REGULAR DRIVER"],
                "residentialAddress": property_address,
                "surname": surname,
            },
            
            "policyholderIsDriver": self.YES_NO_DICT.get("Yes"),
            "addListedDriver": self._map_value(self.input_data.get("SECOND_DRIVER_FLAG"),self.YES_NO_DICT,log_field="SECOND_DRIVER_FLAG")
        
        }

        return translation


    def translation_input_data_page_12(self,property_address):
        first_name = self.input_data.get("FIRST_NAME_DUMMY2")
        surname = self.input_data.get("LAST_NAME_DUMMY2")

        age = self._safe_int(self.input_data.get("PC_AGE2"))
        license_held_for_years = self._safe_int(self.input_data.get("PC_YEARSHELDLICENSE2"))

        license_age = age - license_held_for_years

        date_of_birth_day = self._safe_int(self.input_data.get("DOB_DUMMY_DAY2"))
        date_of_birth_month = self._safe_int(self.input_data.get("DOB_DUMMY_MONTH2"))
        date_of_birth_year = self._safe_int(self.input_data.get("DOB_DUMMY_YEAR2"))

        dt = datetime(date_of_birth_year, date_of_birth_month, date_of_birth_day)
        date_of_birth = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


        translation = {
                "accessToAnotherVehicle": self.YES_NO_DICT.get("No"),
                "birthDate": date_of_birth,
                "driverType": "LISTED DRIVER",
                "employmentStatus": self._map_value(self.input_data.get("EMPLOYMENT2"),self.EMPLOYMENT_TYPE,log_field="EMPLOYMENT2"),
                "occupationIndustry": self._map_value(self.input_data.get("OCCUPATION2"),self.OCCUPATION_TYPE,log_field="OCCUPATION2"),
                "firstName": first_name,
                "gender": self._map_value(self.input_data.get("PG_GENDER2"),self.GENDERS,log_field="PG_GENDER2",),
                "hasPreviousIncidents": self.YES_NO_DICT.get("No"),
                "licenceDetails": {
                    "anyDemeritPoints": self._map_value("0", self.DRIVING_DEMERITS, log_field="DRIVING_DEMERITS"),
                    "hasOtherInfringements": self.YES_NO_DICT.get("No"),
                    "licenceAge": license_age,
                    "licenceType": self._map_value(self.input_data.get("LICENCE_TYPE2"),self.LICENSE_MAPPING,log_field="LICENCE_TYPE2",)
                },
                "personKey": "NEW",
                "personTypes": ["REGULAR DRIVER"],
                "previousIncidents": [],
                "residentialAddress":property_address,
                "surname": surname,
                "useMobilePhone": self._map_value("Never", self.MOBILE_PHONE_USAGE, log_field="MOBILE_PHONE_USAGE")
            }

        return translation

    def translation_input_data_page_13(self):
        translation = {
            "hasPreviousCriminalConvictions": self.YES_NO_DICT.get("No"),
            "hasPreviousInsuranceCancelled": self.YES_NO_DICT.get("No"),
            "previousCancellations":[]
        }

        return translation

class BaseYouiCrawler:

    def __init__(self):
        self.headers = {}
        self.cookies = {}
        self.flow_instance_id = None
        self.base_json_data = {}
        self.message = None
        self.output_data_dict = {}
        self.csv_file = ""
        self.input_data = {}

    def make_request(self, url, method="GET", payload=None, retries=5, delay=2):

        YOUR_API_KEY = "4d39f5403fa24cf6a31cf8873b3d9101"
        go_proxy = "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080"
        oxy_lab_proxy = "http://customer-wmc_user_aakFe:WmcLetmein_123@pr.oxylabs.io:10002"
        zyte_data_api = f"http://{YOUR_API_KEY}:@api.zyte.com:8011/"

        proxies = {"http": zyte_data_api, "https": zyte_data_api}

        non_retryable_statuses = {400, 401}
        delay_time = random.uniform(delay, delay - 3)
        # logger.info(f"Sleeping for {delay_time:.2f} seconds before request")
        # time.sleep(delay_time)

        for attempt in range(1, retries + 1):
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=self.headers,
                    cookies=self.cookies,
                    json=payload,
                    proxies=proxies,
                    timeout=180,
                    verify=False,
                )

                # Handle non-retryable statuses right away
                if response.status_code in non_retryable_statuses:
                    if response.status_code == 400 and "getAddress" in url:
                        return None  # caller retries with a fresh moniker

                    elif "addressSelectorConditionValidator" in response.text:
                        self.message = "Address Not Found"
                        self.search_for_quote_failure()
                        self.write_csv()
                        return None

                    logger.error(
                        f"Non-retryable error {response.status_code}: {response.text}"
                    )
                    response.raise_for_status()

                # Retry only for 403
                if response.status_code == 403:
                    logger.warning(
                        f"Received 403 on attempt {attempt}/{retries} (rate-limited or blocked)."
                    )
                    if attempt < retries:
                        backoff_time = random.uniform(delay * 3, delay * 6)
                        logger.info(
                            f"Backing off for {backoff_time:.2f} seconds before retry."
                        )
                        time.sleep(backoff_time)
                        continue
                    else:
                        logger.error(
                            f"403 persisted after {retries} attempts. URL: {url}"
                        )
                        response.raise_for_status()

                # Any other status: raise if not 200
                response.raise_for_status()

                # Update cookies on success
                if response.status_code == 200:
                    self.cookies.update(dict(response.cookies.items()))
                    logger.info(f"Updated cookies {dict(response.cookies.items())}")

                return response.json()

            except (ReadTimeout, SSLError, ProxyError) as e:
                # retry on SSL EOF errors
                logger.warning(
                    f"SSL error on attempt {attempt}/{retries}: {e}. Retrying..."
                )
                if attempt < retries:
                    time.sleep(random.uniform(delay, delay + 5))
                    continue
                else:
                    logger.error(
                        f"SSL error persisted after {retries} attempts. URL: {url}"
                    )
                    raise

            except requests.RequestException as e:
                # Don’t retry on other exceptions
                logger.error(
                    f"Request failed (non-retryable). URL: {url} Error: {e}",
                    exc_info=True,
                )
                raise

        return None

    def base_json(self):
        json_data = self.make_request(
            url=f"https://quote.youi.com.au/api/quote/{self.flow_instance_id}/currentstep"
        )
        self.base_json_data = {
            "flowInstanceId": self.flow_instance_id,
            "currentStep": "yourCar",
            "quoteData": json_data["quoteData"],
        }
        return json_data

    def update_json(self, response):
        data = {
            "flowInstanceId": self.flow_instance_id,
            "currentStep": response.get("currentStep").get("stepName"),
            "quoteData": response.get("quoteData"),
        }
        return data

    def get_address_by_suburb(self):
        suburb = self.input_data.get("SUBURB")
        postcode = self.input_data.get("POSTCODE")
        address = self.input_data.get("ADDRESS")
        state = self.input_data.get("STATE")

        def search_address(phrase):
            payload = {
                "searchTerm": phrase,
                "flowInstanceId": self.flow_instance_id,
            }
            return self.make_request(
                "https://quote.youi.com.au/api/experian/search",
                method="POST",
                payload=payload,
            )

        address_search_phrase = f"{address}, {suburb} {state} {postcode}"
        fallback_phrase = f"{address}, {suburb}"

        # Experian's getAddress returns a bare 400 intermittently, so re-search with a
        # fresh moniker before giving up, and try the other candidates it offered.
        for attempt in range(1, 4):
            print(f"🔍 Searching with full phrase: {address_search_phrase} (attempt {attempt}/3)")
            candidates = search_address(address_search_phrase)
            if not candidates:
                print(f"⚠️ No results, retrying with fallback phrase: {fallback_phrase}")
                candidates = search_address(fallback_phrase) or []

            for candidate in candidates[:3]:
                payload = {"moniker": candidate.get("moniker"), "flowInstanceId": self.flow_instance_id}
                resolved = self.make_request(
                    "https://quote.youi.com.au/api/experian/getAddress",
                    method="POST",
                    payload=payload,
                )
                if resolved:
                    return {**resolved, **{"experianSearchResult": candidate}}
                print(f"⚠️ getAddress failed for {candidate.get('address')!r}")

        print(f"❌ Address unresolved after 3 attempts: {address_search_phrase}")
        self.message = "Address Not Found"
        self.search_for_quote_failure()
        return None

    def search_for_quote_failure(self):
        quote_data = self.base_json_data.get("quoteData", {})
        if quote_data:
            underwriting = quote_data.get("underwritingRequirements", {})
            if underwriting:
                messages = underwriting.get("messages", [])
                if underwriting.get("stepKey") == "listedDriversOverview":
                    return False

                # if "Before we agree to offer cover, our underwriting team needs to review the information provided. <BR><BR> If you have provided us with a phone number, we will call you to discuss the outcome of the review prior to your final purchase decision. If you haven't provided a contact phone number, please contact us to discuss the outcome of the review." in messages[0].get("message", None):
                #     self.flag = True
                #     return False

                if isinstance(messages, list) and len(messages) > 0:
                    self.message = messages[0].get("message", None)

        if self.message:
            self.output_data_dict["Reason_for_quote_failure"] = self.message
            self.output_data_dict["QuoteLink"] = (
                f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}"
            )
            self.output_data_dict["QuoteNumber"] = self.base_json_data.get(
                "quoteData", {}
            ).get("quoteNumber")
            self.write_csv()
            return True

    def write_csv(self):
        try:
            csv_file = self.csv_file
            isheader = os.path.exists(csv_file)
            result_data = {
                key: value
                for key, value in self.output_data_dict.items()
                if key in self.output_data_dict.keys()
            }
            pd.DataFrame([result_data]).to_csv(
                csv_file, index=False, header=not isheader, mode="a"
            )

            logger.info(f"{self.flow_instance_id} has been appended to {csv_file}.")
        except Exception as e:
            logger.info("error in write_csv -->", e)
            raise e  # Raise the exception to skip the iteration

    def find_best_match(
        self, car_input, car_list, parameter="description", threshold=40
    ):
        car_input = str(car_input)
        # First try exact match
        exact_match = next(
            (car for car in car_list if car[parameter].lower() == car_input.lower()),
            None,
        )
        if exact_match:
            return exact_match

        # If no exact match, use fuzzy matching
        choices = [car[parameter] for car in car_list]
        result = process.extractOne(car_input, choices, scorer=fuzz.ratio)
        if result is not None:
            best_match, score = result[:2]
            if score >= threshold:
                return next(car for car in car_list if car[parameter] == best_match)

        return car_list[0]

    def get_car_make(self, input_data, progress_dict):
        car_make = [
            {
                "makeCode": "FORD",
                "description": "Ford",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HOLD",
                "description": "Holden",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HOND",
                "description": "Honda",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HYUN",
                "description": "Hyundai",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "MAZD",
                "description": "Mazda",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "MITS",
                "description": "Mitsubishi",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "NISS",
                "description": "Nissan",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "SUBA",
                "description": "Subaru",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "TOYO",
                "description": "Toyota",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "VOLK",
                "description": "Volkswagen",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "ABAR",
                "description": "Abarth",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ACE",
                "description": "ACE EV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ADRI",
                "description": "Adria",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ALFA",
                "description": "Alfa Romeo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ALPI",
                "description": "Alpine",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ASTO",
                "description": "Aston Martin",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AUDI",
                "description": "Audi",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AUTO",
                "description": "AUTOTRAIL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AVAN",
                "description": "A'Van",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AVID",
                "description": "Avida",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BENT",
                "description": "Bentley",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BMW",
                "description": "BMW",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BYD",
                "description": "BYD",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CATE",
                "description": "Caterham",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHER",
                "description": "Chery",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHEV",
                "description": "Chevrolet",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHRY",
                "description": "Chrysler",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CITR",
                "description": "Citroen",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "COAC",
                "description": "Coachman RV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CSV",
                "description": "CSV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CUPR",
                "description": "CUPRA",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAEW",
                "description": "Daewoo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAIH",
                "description": "Daihatsu",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAIM",
                "description": "Daimler",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DATS",
                "description": "Datsun",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DEEP",
                "description": "DEEPAL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DODG",
                "description": "Dodge",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "EART",
                "description": "Earthcruiser",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "EUNO",
                "description": "Eunos",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FERR",
                "description": "Ferrari",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FIAT",
                "description": "Fiat",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FORD",
                "description": "Ford",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FPV",
                "description": "Ford Performance Vehicles",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FOTO",
                "description": "Foton",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FREE",
                "description": "Freeway Motor Homes",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FRON",
                "description": "Frontline Campers",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GEEL",
                "description": "Geely",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GEIS",
                "description": "Geist",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GENE",
                "description": "Genesis",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GETA",
                "description": "Getaway",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GREA",
                "description": "Great Wall",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HAVA",
                "description": "HAVAL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HDT",
                "description": "HDT",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HOLD",
                "description": "Holden",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HSV",
                "description": "Holden Special Vehicles",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HOND",
                "description": "Honda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HORI",
                "description": "Horizon",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HUMM",
                "description": "Hummer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYME",
                "description": "Hymer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYUN",
                "description": "Hyundai",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYUT",
                "description": "HYUNDAI TRUCKS",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "INEO",
                "description": "INEOS",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "INFI",
                "description": "Infiniti",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ISUZ",
                "description": "Isuzu",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "IVEC",
                "description": "Iveco",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAC",
                "description": "JAC",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAGU",
                "description": "Jaguar",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAYC",
                "description": "Jayco",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JEEP",
                "description": "Jeep",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JMC",
                "description": "JMC",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JOYL",
                "description": "JOYLONG",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "KIA",
                "description": "Kia",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LADA",
                "description": "Lada",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LAMB",
                "description": "Lamborghini",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LAND",
                "description": "Land Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LDV",
                "description": "LDV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LEAP",
                "description": "LEAPMOTOR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LEXU",
                "description": "Lexus",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LOTU",
                "description": "Lotus",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MG",
                "description": "M.G.",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MAHI",
                "description": "Mahindra",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MASE",
                "description": "Maserati",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MATI",
                "description": "Matilda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MAZD",
                "description": "Mazda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MERC",
                "description": "Mercedes-Benz",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MINI",
                "description": "Mini",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MITS",
                "description": "Mitsubishi",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MORG",
                "description": "Morgan",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MOTE",
                "description": "Motek",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "NISS",
                "description": "Nissan",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "NSV",
                "description": "Nissan Special Vehicle",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "OPEL",
                "description": "Opel",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PARA",
                "description": "Paradise",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PERF",
                "description": "Performax",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PEUG",
                "description": "Peugeot",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "POLE",
                "description": "POLESTAR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PORS",
                "description": "Porsche",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PROT",
                "description": "Proton",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RAM",
                "description": "Ram",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RANG",
                "description": "Range Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RENA",
                "description": "Renault",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ROLL",
                "description": "Rolls Royce",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ROVE",
                "description": "Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SAAB",
                "description": "Saab",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SEAT",
                "description": "Seat",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SKOD",
                "description": "Skoda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SKYW",
                "description": "SKYWELL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SMAR",
                "description": "Smart",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SSAN",
                "description": "Ssangyong",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "KGMS",
                "description": "SSANGYONG KGM",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUBA",
                "description": "Subaru",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUNC",
                "description": "Suncamper Conversions",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUNL",
                "description": "Sunliner",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUZU",
                "description": "Suzuki",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SWAG",
                "description": "Swagman",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SWIF",
                "description": "Swift",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TALV",
                "description": "TALVOR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TATA",
                "description": "Tata",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TESL",
                "description": "Tesla",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TEST",
                "description": "Testoa",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TOYO",
                "description": "Toyota",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TRAK",
                "description": "Trakka",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "VOLK",
                "description": "Volkswagen",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "VOLV",
                "description": "Volvo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "WAND",
                "description": "Wanderer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "WINN",
                "description": "Winnebago",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "XPEN",
                "description": "XPENG",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ZEEK",
                "description": "ZEEKR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ZXAU",
                "description": "ZX Auto",
                "grouping": "vehiclemakesgroupingother",
            },
        ]
        car_input = input_data["PG_GG_MAKE"]
        car_make = self.find_best_match(car_input, car_make, parameter="description")
        progress_dict["car_make"] = car_make
        return car_make

    def get_car_model(self, input_data, progress_dict):
        make_code = progress_dict["car_make"]["makeCode"]
        year_of_manufacture = progress_dict["year_of_manufacture"]
        car_model = input_data["PG_GG_FAMILY"]
        url = f"https://quote.youi.com.au/api/vehicles/{make_code}/{year_of_manufacture}/models"
        response = self.make_request(url)
        match = self.find_best_match(car_model, response, parameter="description")
        progress_dict["get_car_model"] = match
        return match

    def get_car_badge(self, input_data, progress_dict):
        make_code = progress_dict["car_make"]["makeCode"]
        year_of_manufacture = progress_dict["year_of_manufacture"]
        encodedKey = progress_dict["get_car_model"]["encodedKey"]

        if "auto" or "automatic" in str(input_data["PG_GG_FAMILY"]).lower():
            car_type = "Auto"
        else:
            car_type = "Manual"

        car_badge = f"{input_data['PG_GG_BODYSTYLE']} - {car_type} - {input_data['PG_GG_TRANS']} - {input_data['PG_GG_ENGINE']}"
        url = f"https://quote.youi.com.au/api/vehicles/{make_code}/{encodedKey}/{year_of_manufacture}/badges"
        response = self.make_request(url)
        match = self.find_best_match(car_badge, response, parameter="description")
        progress_dict["get_car_badge"] = match
        return match

    def get_car_style(self, input_data, progress_dict):
        make_code = progress_dict["car_make"]["makeCode"]
        year_of_manufacture = progress_dict["year_of_manufacture"]
        encodedKey = progress_dict["get_car_model"]["encodedKey"]
        encodedKey2 = progress_dict["get_car_badge"]["encodedKey"]

        if "awd" in str(input_data["PG_GG_VARIANT"]).lower():
            car_type = "awd"
        else:
            car_type = input_data["PG_GG_VARIANT"]
        url = f"https://quote.youi.com.au/api/vehicles/{make_code}/{encodedKey}/{year_of_manufacture}/{encodedKey2}/styles"
        response = self.make_request(url)
        car_style = f"{input_data['PG_GG_BODYSTYLE']} - {car_type} - {input_data['PG_GG_ENGSIZE']} - {input_data['PG_GG_ENGINE']}"
        match = self.find_best_match(car_style, response, parameter="description")
        progress_dict["get_car_style"] = match
        return match

    def get_car_description(self, input_data, progress_dict):
        make_code = progress_dict["car_make"]["makeCode"]
        year_of_manufacture = progress_dict["year_of_manufacture"]
        encodedKey = progress_dict["get_car_model"]["encodedKey"]
        encodedKey2 = progress_dict["get_car_badge"]["encodedKey"]
        encodedKey3 = progress_dict["get_car_style"]["encodedKey"]

        url = f"https://quote.youi.com.au/api/vehicles/{make_code}/{encodedKey}/{year_of_manufacture}/{encodedKey2}/{encodedKey3}/description"
        response = self.make_request(url)
        car_style = f"{input_data['PG_GG_VARIANT']} - {input_data['PG_GG_SERIES']}"
        match = self.find_best_match(car_style, response, parameter="description")
        progress_dict["get_car_description"] = match
        return match

    def build_car_information(self,input_data):
        car_make_list = [
            {
                "makeCode": "FORD",
                "description": "Ford",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HOLD",
                "description": "Holden",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HOND",
                "description": "Honda",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "HYUN",
                "description": "Hyundai",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "MAZD",
                "description": "Mazda",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "MITS",
                "description": "Mitsubishi",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "NISS",
                "description": "Nissan",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "SUBA",
                "description": "Subaru",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "TOYO",
                "description": "Toyota",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "VOLK",
                "description": "Volkswagen",
                "grouping": "vehiclemakesgroupingpopular",
            },
            {
                "makeCode": "ABAR",
                "description": "Abarth",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ACE",
                "description": "ACE EV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ADRI",
                "description": "Adria",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ALFA",
                "description": "Alfa Romeo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ALPI",
                "description": "Alpine",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ASTO",
                "description": "Aston Martin",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AUDI",
                "description": "Audi",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AUTO",
                "description": "AUTOTRAIL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AVAN",
                "description": "A'Van",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "AVID",
                "description": "Avida",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BENT",
                "description": "Bentley",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BMW",
                "description": "BMW",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "BYD",
                "description": "BYD",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CATE",
                "description": "Caterham",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHER",
                "description": "Chery",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHEV",
                "description": "Chevrolet",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CHRY",
                "description": "Chrysler",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CITR",
                "description": "Citroen",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "COAC",
                "description": "Coachman RV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CSV",
                "description": "CSV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "CUPR",
                "description": "CUPRA",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAEW",
                "description": "Daewoo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAIH",
                "description": "Daihatsu",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DAIM",
                "description": "Daimler",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DATS",
                "description": "Datsun",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DEEP",
                "description": "DEEPAL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "DODG",
                "description": "Dodge",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "EART",
                "description": "Earthcruiser",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "EUNO",
                "description": "Eunos",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FERR",
                "description": "Ferrari",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FIAT",
                "description": "Fiat",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FORD",
                "description": "Ford",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FPV",
                "description": "Ford Performance Vehicles",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FOTO",
                "description": "Foton",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FREE",
                "description": "Freeway Motor Homes",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "FRON",
                "description": "Frontline Campers",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GEEL",
                "description": "Geely",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GEIS",
                "description": "Geist",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GENE",
                "description": "Genesis",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GETA",
                "description": "Getaway",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "GREA",
                "description": "Great Wall",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HAVA",
                "description": "HAVAL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HDT",
                "description": "HDT",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HOLD",
                "description": "Holden",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HSV",
                "description": "Holden Special Vehicles",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HOND",
                "description": "Honda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HORI",
                "description": "Horizon",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HUMM",
                "description": "Hummer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYME",
                "description": "Hymer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYUN",
                "description": "Hyundai",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "HYUT",
                "description": "HYUNDAI TRUCKS",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "INEO",
                "description": "INEOS",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "INFI",
                "description": "Infiniti",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ISUZ",
                "description": "Isuzu",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "IVEC",
                "description": "Iveco",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAC",
                "description": "JAC",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAGU",
                "description": "Jaguar",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JAYC",
                "description": "Jayco",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JEEP",
                "description": "Jeep",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JMC",
                "description": "JMC",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "JOYL",
                "description": "JOYLONG",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "KIA",
                "description": "Kia",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LADA",
                "description": "Lada",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LAMB",
                "description": "Lamborghini",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LAND",
                "description": "Land Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LDV",
                "description": "LDV",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LEAP",
                "description": "LEAPMOTOR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LEXU",
                "description": "Lexus",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "LOTU",
                "description": "Lotus",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MG",
                "description": "M.G.",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MAHI",
                "description": "Mahindra",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MASE",
                "description": "Maserati",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MATI",
                "description": "Matilda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MAZD",
                "description": "Mazda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MERC",
                "description": "Mercedes-Benz",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MINI",
                "description": "Mini",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MITS",
                "description": "Mitsubishi",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MORG",
                "description": "Morgan",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "MOTE",
                "description": "Motek",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "NISS",
                "description": "Nissan",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "NSV",
                "description": "Nissan Special Vehicle",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "OPEL",
                "description": "Opel",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PARA",
                "description": "Paradise",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PERF",
                "description": "Performax",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PEUG",
                "description": "Peugeot",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "POLE",
                "description": "POLESTAR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PORS",
                "description": "Porsche",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "PROT",
                "description": "Proton",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RAM",
                "description": "Ram",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RANG",
                "description": "Range Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "RENA",
                "description": "Renault",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ROLL",
                "description": "Rolls Royce",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ROVE",
                "description": "Rover",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SAAB",
                "description": "Saab",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SEAT",
                "description": "Seat",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SKOD",
                "description": "Skoda",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SKYW",
                "description": "SKYWELL",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SMAR",
                "description": "Smart",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SSAN",
                "description": "Ssangyong",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "KGMS",
                "description": "SSANGYONG KGM",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUBA",
                "description": "Subaru",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUNC",
                "description": "Suncamper Conversions",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUNL",
                "description": "Sunliner",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SUZU",
                "description": "Suzuki",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SWAG",
                "description": "Swagman",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "SWIF",
                "description": "Swift",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TALV",
                "description": "TALVOR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TATA",
                "description": "Tata",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TESL",
                "description": "Tesla",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TEST",
                "description": "Testoa",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TOYO",
                "description": "Toyota",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "TRAK",
                "description": "Trakka",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "VOLK",
                "description": "Volkswagen",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "VOLV",
                "description": "Volvo",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "WAND",
                "description": "Wanderer",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "WINN",
                "description": "Winnebago",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "XPEN",
                "description": "XPENG",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ZEEK",
                "description": "ZEEKR",
                "grouping": "vehiclemakesgroupingother",
            },
            {
                "makeCode": "ZXAU",
                "description": "ZX Auto",
                "grouping": "vehiclemakesgroupingother",
            },
        ]

        input_car_make = input_data["PG_GG_MAKE"]
        input_car_year = input_data["VEHICLE_YEAR"]
        input_car_family = input_data["PG_GG_FAMILY"]
        input_car_variant = input_data["PG_GG_VARIANT"]
        input_car_series = input_data["PG_GG_SERIES"]
        input_car_bodystyle = input_data["PG_GG_BODYSTYLE"]
        input_car_transmission = input_data["PG_GG_TRANS"]
        input_car_engine_size = input_data["PG_GG_ENGSIZE"]
        input_car_engine = input_data["PG_GG_ENGINE"]
        input_colour = input_data["COLOUR"]

        COLORS = [
            {"key": "516", "value": "Beige"},
            {"key": "509", "value": "Black"},
            {"key": "510", "value": "Blue"},
            {"key": "521", "value": "Brown"},
            {"key": "523", "value": "Burgundy"},
            {"key": "525", "value": "Charcoal"},
            {"key": "522", "value": "Gold"},
            {"key": "511", "value": "Green"},
            {"key": "526", "value": "Grey"},
            {"key": "520", "value": "Maroon"},
            {"key": "512", "value": "Orange"},
            {"key": "524", "value": "Pink"},
            {"key": "513", "value": "Purple"},
            {"key": "514", "value": "Red"},
            {"key": "519", "value": "Silver"},
            {"key": "10661", "value": "Champagne"},
            {"key": "515", "value": "White"},
            {"key": "518", "value": "Yellow"},
        ]
        
        best_match_car_make = self.find_best_match(input_car_make, car_make_list, parameter="description")
        car_make_code = best_match_car_make.get('makeCode')
        car_make_name = best_match_car_make.get('description')
        print(car_make_name)
        print(input_car_year)
        
        car_model_list = self.make_request(f"https://quote.youi.com.au/api/vehicles/{car_make_code}/{input_car_year}/models")
        best_match_car_model = self.find_best_match(input_car_family, car_model_list, parameter="description")
        model_encoded_key = best_match_car_model.get('encodedKey')
        model_key = best_match_car_model.get('key')
        print(model_key)
        
        car_type_list = self.make_request(f"https://quote.youi.com.au/api/vehicles/{car_make_code}/{model_encoded_key}/{input_car_year}/badges")
        car_type_search_phrase = f"{input_car_variant} - {input_car_bodystyle} - {input_car_transmission}"
        best_match_car_type = self.find_best_match(car_type_search_phrase, car_type_list, parameter="description")
        type_encoded_key = best_match_car_type.get('encodedKey')
        type_key = best_match_car_type.get('key')
        print(type_key)
        
        car_style_list = self.make_request(f"https://quote.youi.com.au/api/vehicles/{car_make_code}/{model_encoded_key}/{input_car_year}/{type_encoded_key}/styles")
        car_style_search_phrase = f"{input_car_transmission} - {input_car_engine_size}"
        best_match_car_style = self.find_best_match(car_style_search_phrase, car_style_list, parameter="description")
        style_encoded_key = best_match_car_style.get('encodedKey')
        style_key = best_match_car_style.get('key')
        print(style_key)
        
        car_description_list = self.make_request(f"https://quote.youi.com.au/api/vehicles/{car_make_code}/{model_encoded_key}/{input_car_year}/{type_encoded_key}/{style_encoded_key}/description")
        car_description_search_phrase = f"{input_car_variant} - {input_car_series}"
        best_match_car_description = self.find_best_match(car_description_search_phrase, car_description_list, parameter="description")
        description_key = best_match_car_description.get('key')
        description = best_match_car_description.get('description')
        print(description_key)
        print(description)

        colour_object = self.find_best_match(input_colour, COLORS, parameter="value")
        colour_code = colour_object.get('key')
        colour_name = colour_object.get('value')
        print(colour_name)
        print(input_car_year)
        
        
        progress_dict = {"car_make_code":car_make_code,"car_make_name":car_make_name,"car_make_year":input_car_year,"car_model_key":model_key,'car_badge':type_key,'car_style':style_key,'car_description':description,"colour":colour_name}
        return progress_dict
    
    def buisness_usage(self,search_string):
        response = self.make_request(f'https://quote.youi.com.au/api/industries?searchString={search_string}')
        best_match = self.find_best_match(search_string, response, parameter="industryName")

        return best_match
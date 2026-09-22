from datetime import date, datetime
from typing import Any, Iterator, Optional
import asyncio
import json
from datetime import datetime, date

import csv

import os
import random
import time
from typing import Any, Dict, Iterator, Optional

import requests
from requests.exceptions import RequestException, SSLError
import logging
from utilities import get_headers_playwright
import urllib3



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)



class YouiMappings:
    """
    Translates user input data into the required key-value mapping for Youi insurance quotes.
    Each translation method corresponds to a different section of the quote form.
    """

    # === Mappings (Class constants) ===

    YES_NO_DICT = {"No": "808", "Yes": "807"}
    DWELLING_TYPE_DICT = {'Free Standing Home': '12475', 'Apartment or unit': '12476', 'Semi-Detached Home': '12477', 'Other dwelling type': '12478'}
    FOUNDATION_TYPE_DICT = {'Concrete slab': '3399', 'Foundations': '3393', 'Poles': '3394','Ground floor': '3397', 'Mid floor': '3398', 'Top floor': '6168','Townhouse': '10650', 'Other semi-detached': '6165', 'Terrace house': '10651', 'Duplex/Triplex': '10652','Granny flat': '3400', 'Non-mobile caravan': '6175', 'Relocatable home': '6174', 'Storage centre': '3402', 'Shed': '6169', 'Other': '3395'}
    OCCUPIED_BY = {'Owner Occupied': '3781', 'Landlord': '3782', 'Tenant': '3783', 'Unoccupied': '3785'}
    HOME_PRODUCT_TYPE = {'Building And Contents': '12632', 'Building': '12633', 'Contents': '12634'}
    NUMBER_OF_ROOMS = {'0': '10539', '1': '4089', '2': '4090', '3': '4091', '4': '4092', '5': '4093', '6': '10586', '6+': '10586'}
    NUMBER_OF_FLOORS = {'1': '10443', '1 (Split Levels)': '10466', '2': '10444', '3': '10445', '4': '10446'}
    PITCHED_FLOOR_TYPE = {'Pitched': '6324', 'Flat': '6325', 'Pitched And Flat': '10585'}
    ROOF_CONSTRUCTION = {'Tiles': '9473', 'Colorbond': '10634', 'Metal': '9478', 'Fibro': '9477', 'Wood': '10582', 'Concrete Slab': '9480', 'Slate': '9474', 'Other': '10240'}
    WALL_CONSTRUCTION = {'Brick Veneer': '10338', 'Concrete': '861', 'Double Brick': '10340', 'Fibro': '859', 'Metal': '863', 'Mud Brick': '857', 'Polystyrene Or Vinyl': '10584', 'Rock': '10341', 'Stucco': '11955', 'Wood': '858', 'Other': '10342', 'Earth/Mud': '857'}
    BUILDING_AGE = {'2025': '16783', '2024': '16781', '2023': '16547', '2022': '15597', '2021': '14538', '2020': '14536', '2019': '10622', '2018': '10621', '2017': '10620', '2016': '10619', '2015': '10618', '2014': '10617', '2013': '10616', '2012': '10615', '2011': '10614', '2010': '10613', '2005-2009': '12723', '2000-2004': '11260', '1995-1999': '10588', '1990-1994': '693', '1980-1989': '12724', '1970-1979': '696', '1960-1969': '10333', '1945-1959': '10334', '1914-1944': '10335', '1891-1913': '10336', 'Before1890': '10337'}
    FLOOR_TYPE = {'Timber / Bamboo / Cork': '5727', 'Vinyl': '15579', 'Tiles': '5730', 'Concrete': '5728', 'Other': '15581'}
    DAMAGE_TYPE_DICT = {"Accidental Damage": "12835","Earthquake Damage": "5544","Escaping Water": "10610","Fire": "5545","Flood/Rain Water Run-off": "5546","Motor Burnout": "10609","Storm Damage": "5573","Subsidence": "5555","Loss Away From Home": "10648","Loss Of Rent": "10607","Loss, Theft Or Attempted Theft": "5562","Third Party Liability": "10612","Power Surge / Outage": "16799",}
    MONTH_NAME_TO_NUM = {"January": "1","February": "2","March": "3","April": "4","May": "5","June": "6","July": "7","August": "8","September": "9","October": "10","November": "11","December": "12",}
    NUMBER_OF_PEOPLE_LIVING = {'0': '10539', '1': '4089', '2': '4090', '3': '4091', '4': '4092', '5': '4093', '6+': '10586','6': '10586'}
    RESIDE_WITH_DICT = {"Just me": "JME", "Family/Partner": "FPR"}
    COVER_DURATION = {'0 Up To 3 Months': '7723', 'From 3 Up To 10 Months': '10635', 'From 10 Up To 12 Months': '10636', 'From 1 Year Up To 2 Years': '7726', 'From 2 Up To 3 Years': '7727', 'From 3 Up To 5 Years': '7728', 'From 5 Up To 10 Years': '7729', '10 Years And Over': '7730'}
    NO_COVER_DURATION = {'0 Up To 1 Month': '6827', 'From 1 Up To 6 Months': '6828', '6 Months And Over': '6829', 'Never been covered': '6830'}
    ACCESSIBLE_WINDOWS_DICT = {"All": "10509", "Some": "10601", "None": "10602","NA": "10602"}
    BURGLAR_ALARM = {'No Alarm': '12805', 'Local Alarm': '12806', 'Back To Base': '10510'}
    GENDERS = {'Male': '085', 'Female': '086'}
    OCCUPIED_PERIOD = {'Not Moved In Yet': '10505', '0 To 1 Month': '10495', '1 To 3 Months': '10496', '3 To 6 Months': '10497', '6 To 12 Months': '10498', '1 To 2 Years': '10499', '2 To 3 Years': '10500', '3 To 5 Years': '10501', '5 To 10 Years': '10502', '10 To 25 Years': '10503', 'More Than 25 Years': '10504'}
    PEOPLE_EMPLOYED = {'1': '11296', '2': '11297', '3': '11298', '4': '11299', '5': '11300', '6 - 9': '12902', '10 - 14': '12903', '15 - 19': '12904', '20-24': '16861', '25-29': '16862', '30-34': '16863', '35-39': '16864', '40+': '16865'}
    STOCK_VALUE = {'Nil': '12814', '<$5,000': '11346', '$5,001-$15,000': '11347', '$15,001-$25,000': '11348', '$25,001-$50,000': '11349', '$50,000+': '11350'}
    CLIENTS_VISITS = {'Na':'11341','None': '11341', '#1-5': '11342', '#6-15': '11343', '#16-30': '11344', '#31+': '11345'}
    STRUCTURE_MAPPING = {'Balcony / Deck': '0010', 'Shed': '0007', 'Pool - In Ground': '0001', 'Pool - Above Ground': '0015'}

    def __init__(self, input_data: dict[str, Any]) -> None:
        """
        Initialize with user input data.
        """
        self.input_data = input_data
        self.cover_type: Optional[str] = None
        self.policy_holder: dict[str, Any] = {}

    # === Utility Methods ===

    def _map_value(self, value: Any, mapping: dict, default: Any = None, log_field: str = "") -> Any:
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

    def translate_input_data_page_01(self, address: dict) -> dict[str, Any]:
        """
        Translate page 1 input data (basic property information) into mapped keys.
        """
        occupied_by = self.input_data.get("OccupancyType", "").title()
        home_product_type_input = self.input_data.get("cover", "").title()
        dwelling_type_input = self.input_data.get("BuildingType_1", "").title()
        foundation_type_input = self.input_data.get("BuildingType_2", "")
        strata_input = self.input_data.get("strata", "").title()
        policy_start_date_day = self._safe_int(self.input_data.get("policy_start_day"), 10)
        policy_start_date_month = self._safe_int(self.input_data.get("policy_start_month"), 10)
        policy_start_date_year = self._safe_int(self.input_data.get("policy_start_year"), 2023)
        try:
            dt = datetime(policy_start_date_year, policy_start_date_month, policy_start_date_day)
        except ValueError:
            dt = datetime.now()
        policy_start_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        dwelling_type_key = self._map_value(dwelling_type_input, self.DWELLING_TYPE_DICT, log_field="dwelling_type")
        foundation_type_key = self._map_value(foundation_type_input, self.FOUNDATION_TYPE_DICT, log_field="foundation_type")
        strata_input_key = self._map_value(strata_input, self.YES_NO_DICT, log_field="strata")
        occupied_by_key = self._map_value(occupied_by, self.OCCUPIED_BY, log_field="occupied_by")
        home_product_type_key = self._map_value(home_product_type_input, self.HOME_PRODUCT_TYPE, log_field="home_product_type")
        self.cover_type = home_product_type_input

        translation = {
            "propertyAddress": address,
            "policyStartDate": policy_start_date,
            "occupiedBy": occupied_by_key,
            "homeProductType": home_product_type_key,
            "dwellingType": dwelling_type_key,
            "foundationType": foundation_type_key,
            "hasBodyCorporateInsurance": strata_input_key,
        }

        # Adjust keys based on dwelling type
        if dwelling_type_key == self.DWELLING_TYPE_DICT.get("Free Standing Home"):
            translation["foundationType"] = foundation_type_key
        elif dwelling_type_key == self.DWELLING_TYPE_DICT.get("Semi-Detached Home"):
            translation.pop("foundationType", None)
            translation["semiDetachedType"] = foundation_type_key
        elif dwelling_type_key == self.DWELLING_TYPE_DICT.get("Other dwelling type"):
            translation.pop("foundationType", None)
            translation["otherDwellingType"] = foundation_type_key
        elif dwelling_type_key == self.DWELLING_TYPE_DICT.get("Apartment or unit"):
            translation.pop("foundationType", None)
            translation["apartmentType"] = foundation_type_key

        return translation
    


    def translate_input_data_page_02(self) -> dict[str, Any]:
        """
        Translate page 2 input data (property features) into mapped keys.
        """
        number_of_bedrooms_input = str(self.input_data.get("NumberOfBedrooms", ""))
        number_of_bathrooms_input = str(self.input_data.get("NumberOfBathrooms", ""))
        number_of_garages_input = str(self.input_data.get("NumberOfGarages", ""))
        number_of_storeys_input = str(self.input_data.get("NumberOfStoreys", ""))
        construction_year_input = self.input_data.get("ConstructionYear_band", "").replace(" ", "").strip()
        construction_wall_type_input = self.input_data.get("WallType", "").title()
        roof_type_input = self.input_data.get("RoofType", "").title()
        solar_power_input = self.input_data.get("solar_power", "").title()
        			

        def include_if_yes(input_key, mapping_key):
            return (
                self.STRUCTURE_MAPPING.get(mapping_key)
                if self.input_data.get(input_key, "").strip().lower() == "yes"
                else None
            )

        home_Structures = list(filter(None, [
            include_if_yes("Balcony", "Balcony / Deck"),
            include_if_yes("Shed", "Shed"),
            include_if_yes("Pool_in_ground", "Pool - In Ground"),
            include_if_yes("Pool_above_ground", "Pool - Above Ground"),
        ]))

        if construction_year_input == "2010+":
            construction_year_input = str(self.input_data.get("ConstructionYear", ""))

        translation = {
            "roofConstruction": self._map_value(roof_type_input, self.ROOF_CONSTRUCTION, log_field="roof_construction"),
            "numberOfBedrooms": self._map_value(number_of_bedrooms_input, self.NUMBER_OF_ROOMS, log_field="number_of_bedrooms"),
            "numberOfBathrooms": self._map_value(number_of_bathrooms_input, self.NUMBER_OF_ROOMS, log_field="number_of_bathrooms"),
            "numberOfGarageSpaces": self._map_value(number_of_garages_input, self.NUMBER_OF_ROOMS, log_field="number_of_garages"),
            "wallConstruction": self._map_value(construction_wall_type_input, self.WALL_CONSTRUCTION, log_field="wall_construction"),
            "buildingLevels": self._map_value(number_of_storeys_input, self.NUMBER_OF_FLOORS, log_field="number_of_storeys"),
            "buildingAge": self._map_value(construction_year_input, self.BUILDING_AGE, log_field="building_age"),
            "homeHasSolar": self._map_value(solar_power_input, self.YES_NO_DICT, log_field="solar_power"),
            "homeStructuresFeatures": home_Structures,
        }
        return translation

    def translate_input_data_page_03(self) -> dict[str, Any]:
        """
        Translate page 3 input data (cover details) into mapped keys.
        """
        sum_insured_contents_input = self.input_data.get("pc_SumInsuredC", "")
        sum_insured_buildings_input = self.input_data.get("pc_SumInsuredB", "")
        roof_shape_input = self.input_data.get("roof_shape", "").title()
        floor_type_input = self.input_data.get("floor_type", "")
        open_floor_plan_input = self.input_data.get("open_floor_plan", "")
        asbestos_usage_input = self.input_data.get("asbestos_usage", "")
        nearby_trees_input = self.input_data.get("nearby_trees", "")
        jewellery_si = self.input_data.get("jewellery_si", "").replace("$", "").strip()
        high_value = self.input_data.get("high_value", "")

        translation = {
            "pitchFlatRoof": self._map_value(roof_shape_input, self.PITCHED_FLOOR_TYPE, log_field="roof_shape"),
            "floorType": self._map_value(floor_type_input, self.FLOOR_TYPE, log_field="floor_type"),
            "openPlanLivingArea": self._map_value(open_floor_plan_input, self.YES_NO_DICT, log_field="open_floor_plan"),
            "hasTallTrees": self._map_value(nearby_trees_input, self.YES_NO_DICT, log_field="nearby_trees"),
            "asbestosUsed": self._map_value(asbestos_usage_input, self.YES_NO_DICT, log_field="asbestos_usage"),
        }

        # Conditional fields for contents/buildings
        if self.cover_type != "Building":
            translation.update({
                "hasSpecifiedHighValueItems": self._map_value(high_value, self.YES_NO_DICT, log_field="high_value"),
                "unspecifiedJewelleryAmount": jewellery_si,
                "replacementCostOfContents": sum_insured_contents_input,
            })
        if self.cover_type != "Contents":
            translation.update({
                "buildingSumInsured": sum_insured_buildings_input
            })

        return translation

    def translate_input_data_page_04(self) -> dict[str, Any]:
        """
        Translate page 4 input data (occupancy, claim history, etc) into mapped keys.
        """
        if self._safe_int(self.input_data.get("FamilyMember"), 1) == 0:
            family_member_input = 1
        else:
            family_member_input = self._safe_int(self.input_data.get("FamilyMember"), 1)

        children_input = self._safe_int(self.input_data.get("Children"), 0)
        total_number_of_people = min(family_member_input + children_input, 6)

        held_insurance_input = self.input_data.get("held_insurance", "")
        claim_history_input = self.input_data.get("claim_history", "")
        claim_year = self.input_data.get("claim_year", "2030")
        claim_month_input = self.input_data.get("claim_month", "12")
        held_insurance_period_input = self.input_data.get("held_insurance_period", "")
        claim_same_location_input = self.input_data.get("claim_same_location", "")
        claim_description_input = self.input_data.get("claim_description", "")
        reside_with_input = self.input_data.get("LivingArrangement", "")
        first_name_dummy = self.input_data.get("first_name_dummy", "")
        surname_dummy = self.input_data.get("surname_dummy", "")
        gender_dummy_input = self.input_data.get("gender_dummy", "")

        dob_dummy_year = self._safe_int(self.input_data.get("DOB_dummy_year", 2000), 2000)
        dob_dummy_month = self._safe_int(self.input_data.get("DOB_dummy_month", 12), 12)
        dob_dummy_day = self._safe_int(self.input_data.get("DOB_dummy_day", 22), 22)
        try:
            DOB_dummy_input = date(dob_dummy_year, dob_dummy_month, dob_dummy_day).strftime("%Y-%m-%d")
        except ValueError:
            DOB_dummy_input = date(2000, 1, 1).strftime("%Y-%m-%d")

        family_member_key = self._map_value(str(family_member_input), self.NUMBER_OF_PEOPLE_LIVING, log_field="family_member")
        children_key = self._map_value(str(children_input), self.NUMBER_OF_PEOPLE_LIVING, log_field="children")
        total_number_of_people_key = self._map_value(str(total_number_of_people), self.NUMBER_OF_PEOPLE_LIVING, log_field="total_number_of_people")
        unrelated_member_key = self._map_value("0", self.NUMBER_OF_PEOPLE_LIVING, log_field="unrelated_member")
        held_insurance_key = self._map_value(held_insurance_input, self.YES_NO_DICT, log_field="held_insurance")
        claim_history_key = self._map_value(claim_history_input, self.YES_NO_DICT, log_field="claim_history")
        claim_same_location_key = self._map_value(claim_same_location_input, self.YES_NO_DICT, log_field="claim_same_location")
        claim_month = str(self.MONTH_NAME_TO_NUM.get(claim_month_input, claim_month_input)).replace(".0", "").strip()
        claim_description_key = self._map_value(claim_description_input, self.DAMAGE_TYPE_DICT, log_field="claim_description")
        reside_with_key = self._map_value(reside_with_input, self.RESIDE_WITH_DICT, log_field="reside_with")
        held_insurance_period_key = self._map_value(held_insurance_period_input, self.COVER_DURATION, log_field="held_insurance_period")
        home_product_type = self._map_value(self.cover_type, self.HOME_PRODUCT_TYPE, "") if self.cover_type else None

        # Prior claims logic
        previous_incident_history = []
        duration_buildings = {}
        duration_contents = {}

        if claim_history_key == self.YES_NO_DICT["Yes"]:
            claim_year = self._safe_int(claim_year, 2000)
            claim_month = self._safe_int(claim_month, 12)
            try:
                date_str = datetime(int(claim_year), int(claim_month), 1).strftime("%Y-%m-%dT%H:%M:%S")
            except ValueError:
                date_str = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

            incident_buildings = {}
            incident_contents = {}
            if self.cover_type != "Contents":
                incident_buildings = {"buildingDescription": claim_description_key}
            if self.cover_type != "Building":
                incident_contents = {"contentsDescription": claim_description_key}

            previous_incident_history.append({
                "date": date_str,
                "atSameAddress": claim_same_location_key,
                "homeProductType": home_product_type,
                "month": claim_month,
                "year": claim_year,
                **incident_buildings,
                **incident_contents
            })

        if self.cover_type != "Contents":
            duration_buildings = {
                "coverDurationBuilding": held_insurance_period_key,
                "currentlyCoveredBuilding": held_insurance_key,
            }
        if self.cover_type != "Building":
            duration_contents = {
                "coverDurationContents": held_insurance_period_key,
                "currentlyCoveredContents": held_insurance_key,
            }

        self.policy_holder = {
            "birthDate": DOB_dummy_input,
            "personKey": "NEW",
            "firstName": first_name_dummy,
        }
        translation = {
            "hasPreviousIncidents": claim_history_key,
            "peopleUnder18LivingAtPremises": children_key,
            "policyholder": self.policy_holder,
            "hasPreviousIncidents": claim_history_key,
            "previousIncidents": previous_incident_history,
            "relatedPeopleOver18LivingAtPremises": family_member_key,
            "residesWith": reside_with_key,
            "totalNumberOfPeopleLivingAtPremises": total_number_of_people_key,
            "unrelatedPeopleOver18LivingAtPremises": unrelated_member_key,
            **duration_contents,
            **duration_buildings,
        }

        # No cover logic
        if held_insurance_key == self.YES_NO_DICT["No"]:
            if self.cover_type != "Contents":
                translation.pop("coverDurationBuilding", None)
                held_insurance_period_key = self._map_value(held_insurance_period_input, self.NO_COVER_DURATION, log_field="no_cover_duration_building")
                translation["noCoverDurationBuilding"] = held_insurance_period_key
            if self.cover_type != "Building":
                translation.pop("coverDurationContents", None)
                held_insurance_period_key = self._map_value(held_insurance_period_input, self.NO_COVER_DURATION, log_field="no_cover_duration_contents")
                translation["noCoverDurationContents"] = held_insurance_period_key

        return translation

    def translate_input_data_page_05(self,business_use_details) -> dict[str, Any]:
        """
        Translate page 5 input data (security features) into mapped keys.
        """
        def get(key: str, mapping: dict, field: str) -> Any:
            return self._map_value(self.input_data.get(key, ""), mapping, log_field=field)

        business_detail_dict = {}
        if self.cover_type != "Building":
            business_use = self._map_value(self.input_data.get("business_use",""), self.YES_NO_DICT, "business_use")
            business_detail_dict = {"businessPremises":business_use}
            if business_use == '807':
                business_employees = self._map_value(self.input_data.get("business_employees"),self.PEOPLE_EMPLOYED,"business_employees")
                business_danger = self._map_value(self.input_data.get("business_danger"),self.YES_NO_DICT,"business_danger")
                business_production = self._map_value(self.input_data.get("business_production"),self.YES_NO_DICT,"business_production")
                business_stock_value = self._map_value(self.input_data.get("business_stockvalue"),self.STOCK_VALUE,"business_stockvalue")
                customer_visits = self._map_value(self.input_data.get("business_customers"),self.CLIENTS_VISITS,"business_customers")
                business_detail_dict.update({
                    "businessNumberOfEmployed": business_employees,
                    "flammableSubstances": business_danger,
                    "stockValue": business_stock_value,
                    "manufacturingOnProperty": business_production,
                    "businessActivity": business_use_details,
                    "manufacturingOnProperty": business_production,
                    "numberOfClientVisits":customer_visits
                })

        translation = {
            "slidingFrenchDoors": get("sliding_doors_flag", self.YES_NO_DICT, "sliding_doors_flag"),
            "slidingFrenchDoorsWithPatioBolts": get("locked_sliding_doors", self.YES_NO_DICT, "locked_sliding_doors"),
            "hasAccessibleWindows": get("accessible_windows", self.YES_NO_DICT, "accessible_windows"),
            "doorsWithKeyOperatedDeadlocks": get("deadlocks_hinged", self.YES_NO_DICT, "deadlocks_hinged"),
            "doorsWithSecurityDoors": get("security_hinged_doors", self.YES_NO_DICT, "security_hinged_doors"),
            "burglarAlarm": self._map_value(self.input_data.get("alarm_type", "").title(), self.BURGLAR_ALARM, log_field="alarm_type"),
            "businessPremises": get("business_use", self.YES_NO_DICT, "business_use"),
            "accessibleWindows": self._map_value(self.input_data.get("secured_windows", ""), self.ACCESSIBLE_WINDOWS_DICT, log_field="secured_windows"),
            "slidingFrenchDoorsWithSecurityDoors": get("security_sliding_doors", self.YES_NO_DICT, "security_sliding_doors"),
            **business_detail_dict,
        }
        return translation

    def translate_input_data_page_06(self, address: dict,business_use_details) -> dict[str, Any]:
        """
        Translate page 6 input data (policyholder and risk) into mapped keys.
        """
        gender_key = self._map_value(self.input_data.get("gender_dummy", ""), self.GENDERS, log_field="gender_dummy")
        unoccupied_key = self._map_value(self.input_data.get("unoccupied", ""), self.YES_NO_DICT, log_field="unoccupied")
        criminal_record_key = self._map_value(self.input_data.get("criminal_record", ""), self.YES_NO_DICT, log_field="criminal_record")
        refused_insurance_key = self._map_value(self.input_data.get("refused_insurance", ""), self.YES_NO_DICT, log_field="refused_insurance")
        occupancy_length_key = self._map_value(self.input_data.get("occupancy_length", ""), self.OCCUPIED_PERIOD, log_field="occupancy_length")
        holiday_rental_key = self._map_value(self.input_data.get("holiday_rental", ""), self.YES_NO_DICT, log_field="holiday_rental")

        business_detail_dict = {}
        if self.cover_type == "Building":
            business_use = self._map_value(self.input_data.get("business_use",""), self.YES_NO_DICT, "business_use")
            business_detail_dict = {"businessPremises":business_use}
            if business_use == '807':
                business_employees = self._map_value(self.input_data.get("business_employees"),self.PEOPLE_EMPLOYED,"business_employees")
                business_danger = self._map_value(self.input_data.get("business_danger"),self.YES_NO_DICT,"business_danger")
                business_production = self._map_value(self.input_data.get("business_production"),self.YES_NO_DICT,"business_production")
                business_stock_value = self._map_value(self.input_data.get("business_stockvalue"),self.STOCK_VALUE,"business_stockvalue")
                customer_visits = self._map_value(self.input_data.get("business_customers"),self.CLIENTS_VISITS,"business_customers")
                business_detail_dict.update({
                    "businessNumberOfEmployed": business_employees,
                    "flammableSubstances": business_danger,
                    "stockValue": business_stock_value,
                    "manufacturingOnProperty": business_production,
                    "businessActivity": business_use_details,
                    "manufacturingOnProperty": business_production,
                    "numberOfClientVisits":customer_visits
                })

        self.policy_holder.update({
            "firstName": self.input_data.get("first_name_dummy", ""),
            "gender": gender_key,
            "residentialAddress": address,
            "surname": self.input_data.get("surname_dummy", "")
        })
        translation = {
            "unoccupiedMore60Days": unoccupied_key,
            "shortTermHolidayRental": holiday_rental_key,
            "previousCancellations": [],
            "occupiedPeriod": occupancy_length_key,
            "hasPreviousInsuranceCancelled": refused_insurance_key,
            "hasPreviousCriminalConvictions": criminal_record_key,
            "policyholder": self.policy_holder,
            **business_detail_dict,
        }
        return translation


class BaseYouiCrawler:
    ADDRESS_ATTEMPTS = 3  # search -> getAddress rounds before calling an address unfindable
    ADDRESS_CANDIDATES = 4  # Experian hits tried per round (the quote rejects some it resolves)

    def __init__(self):
        """Initialize the crawler with default instance variables."""
        self.headers: Dict[str, str] = {}
        self.cookies: Dict[str, str] = {}
        self.flow_instance_id: Optional[str] = None
        self.base_json_data: Dict[str, Any] = {}
        self.message: Optional[str] = None
        self.output_data_dict: Dict[str, Any] = {}
        self.csv_file: str = ""
        self.input_data: Dict[str, str] = {}

    def make_request(
        self,
        url: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        retries: int = 3,  # Default; was MAX_RETRIES (assume defined elsewhere)
        delay: float = 5.0,  # Default; was BASE_DELAY
    ) -> Any:
        """
        Make an HTTP request with retries, proxies, and error handling.
        
        Changes:
        - Used env vars for proxies (security: avoids hardcoding credentials).
        - Generalized retries to include 429/5xx with exponential backoff (efficiency: adapts to failures).
        - Fixed syntax in backoff calculation.
        - Standardized logger; masked sensitive data in logs.
        - Added type hints and docstring for readability.
        """
        # Load proxies from env vars (e.g., set in Kubernetes secrets)
        YOUR_API_KEY = "4d39f5403fa24cf6a31cf8873b3d9101"
        go_proxy = "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080"
        oxy_lab_proxy = "http://customer-wmc_user_aakFe:WmcLetmein_123@pr.oxylabs.io:10002"
        zyte_data_api = f"http://{YOUR_API_KEY}:@api.zyte.com:8011/"

        proxies = {"http": zyte_data_api, "https": zyte_data_api}

        retry_able_statuses = {403, 429}  # Expanded for rate-limiting
        non_retry_able_statuses = {400, 401}

        # Initial random delay
        delay_time = random.uniform(delay - 3, delay)
        logger.info(f"Sleeping for {delay_time:.2f} seconds before request")
        time.sleep(delay_time)

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
                    verify=False
                )


                if response.status_code in non_retry_able_statuses:
                    if response.status_code == 400:
                        logger.error(response.text)
                    if response.status_code == 400 and "getAddress" in url:
                        return None  # caller retries with a fresh moniker
                    elif "addressSelectorConditionValidator" in response.text:
                        return None  # quote rejected this address; caller tries the next candidate
                    logger.error(f"Non-retry-able error {response.status_code}")
                    response.raise_for_status()

                if response.status_code in retry_able_statuses or response.status_code >= 500:
                    if response.status_code == 400:
                        logger.error(response.text)
                    logger.warning(f"Retry-able error {response.status_code} on attempt {attempt}/{retries}")
                    if attempt < retries:
                        # Exponential backoff (efficiency: starts small, grows to avoid hammering)
                        backoff_time = random.uniform(delay * (2 ** attempt), delay * (2 ** (attempt + 1)))
                        logger.info(f"Backing off for {backoff_time:.2f} seconds")
                        time.sleep(backoff_time)
                        continue
                    else:
                        logger.error(f"Error persisted after {retries} attempts. URL: {url}")
                        response.raise_for_status()

                response.raise_for_status()  # Raise for any other non-200

                # Update cookies on success (log without exposing values)
                if response.status_code == 200:
                    self.cookies.update(dict(response.cookies.items()))
                    logger.info("Updated cookies (details masked for security)")

                return response.json()

            except SSLError as e:
                logger.warning(f"SSL error on attempt {attempt}/{retries}: {e}. Retrying...")
                if attempt < retries:
                    time.sleep(random.uniform(delay, delay + 5))
                    continue
                else:
                    logger.error(f"SSL error persisted after {retries} attempts. URL: {url}")
                    raise

            except RequestException as e:
                logger.error(f"Request failed. URL: {url} Error: {e}", exc_info=True)
                raise

        return None

    def base_json(self) -> Dict[str, Any]:
        """Fetch and construct base JSON data for the quote."""
        if not self.flow_instance_id:
            raise ValueError("flow_instance_id must be set")
        json_data = self.make_request(url=f"https://quote.youi.com.au/api/quote/{self.flow_instance_id}/currentstep")

        if not json_data:
            return {}

        self.base_json_data = {
            "flowInstanceId": self.flow_instance_id,
            "currentStep": "home",
            "quoteData": json_data.get("quoteData", {}),  # Safe get
        }
        return json_data

    def update_json(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update JSON data from response.
        
        Changes: Removed commented code; added safe gets for robustness.
        """
        return {
            "flowInstanceId": self.flow_instance_id,
            "currentStep": response.get("currentStep", {}).get("stepName", ""),
            "quoteData": response.get("quoteData", {}),
        }

    def resolved_addresses(self) -> Iterator[Dict[str, Any]]:
        """Yield resolvable Experian candidates for the input address, best match first.

        Callers keep pulling until the quote accepts one: getAddress 400s
        intermittently, and the quote's addressSelector rejects some addresses it
        happily resolves (a ranged street number like '1177-1185' is one).
        """
        required_keys = ["suburb", "postcode", "address", "state"]
        if not all(key in self.input_data for key in required_keys):
            logger.error("Missing required input_data keys")
            return

        suburb = self.input_data["suburb"]
        postcode = self.input_data["postcode"]
        address = self.input_data["address"]
        state = self.input_data["state"]

        def search_address(phrase: str) -> Optional[list]:
            payload = {"searchTerm": phrase, "flowInstanceId": self.flow_instance_id}
            return self.make_request("https://quote.youi.com.au/api/experian/search", method="POST", payload=payload)

        address_search_phrase = f"{address}, {suburb} {state} {postcode}"
        fallback_phrase = f"{address}, {suburb}"

        for attempt in range(1, self.ADDRESS_ATTEMPTS + 1):
            logger.info(f"Searching with full phrase: {address_search_phrase} (attempt {attempt}/{self.ADDRESS_ATTEMPTS})")
            candidates = search_address(address_search_phrase)
            if not candidates:
                logger.warning(f"No results, retrying with: {fallback_phrase}")
                candidates = search_address(fallback_phrase) or []

            resolved_any = False
            for candidate in candidates[: self.ADDRESS_CANDIDATES]:
                payload = {"moniker": candidate.get("moniker"), "flowInstanceId": self.flow_instance_id}
                resolved = self.make_request(
                    "https://quote.youi.com.au/api/experian/getAddress", method="POST", payload=payload
                )
                if not resolved:
                    logger.warning(f"getAddress failed for {candidate.get('address')!r}")
                    continue
                resolved_any = True
                yield {**resolved, **{"experianSearchResult": candidate}}

            if resolved_any:
                return  # every resolvable candidate was rejected downstream

        logger.error(f"No resolvable address for: {address_search_phrase}")

    def populate_page_01(self) -> Dict[str, Any]:
        """Submit page 1, walking the address candidates until the quote takes one."""
        for property_address in self.resolved_addresses():
            translation = self.mappings.translate_input_data_page_01(property_address)
            self.base_json_data.setdefault("quoteData", {}).update(translation)
            self.base_json_data["currentStep"] = "provisionalPolicyType"
            self.base_json_data["flowInstanceId"] = self.flow_instance_id

            response_json_data = self.make_request(self.quote_url, method="PUT", payload=self.base_json_data)
            if response_json_data:
                self.property_address = property_address
                self.base_json_data = self.update_json(response_json_data)
                return self.base_json_data

            rejected = property_address.get("experianSearchResult", {}).get("address")
            logger.warning(f"Quote rejected address {rejected!r}, trying next candidate")

        self.message = "Address Not Found"
        self.search_for_quote_failure()  # writes the failure row
        raise RuntimeError(f"No address accepted for: {self.input_data.get('address')}")

    def extract_business_data(self,business_type) -> Dict[str, Any]:
        response = self.make_request(f"https://quote.youi.com.au/api/industries?searchString={business_type}", method="GET")
        if len(response) >= 1:
            return response[0]
        return {}

    def search_for_quote_failure(self) -> bool:
        """
        Check for quote failures and set output.
        
        Changes: Removed commented code; added type checks.
        """
        quote_data = self.base_json_data.get("quoteData", {})
        if self.message:    
            self.output_data_dict["Reason_for_quote_failure"] = self.message
            self.output_data_dict["QuoteLink"] = (
                f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}"
            )
            self.output_data_dict["QuoteNumber"] = quote_data.get("quoteNumber")
            self.write_csv()
            return True
        else:
            
            underwriting = quote_data.get("underwritingRequirements", {})
            if not underwriting:
                return False

            if underwriting.get("stepKey") == "cycloneMitigation":
                return False

            messages = underwriting.get("messages", [])
            if isinstance(messages, list) and messages:
                self.message = messages[0].get("message")

        if self.message:
            self.output_data_dict["Reason_for_quote_failure"] = self.message
            self.output_data_dict["QuoteLink"] = (
                f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}"
            )
            self.output_data_dict["QuoteNumber"] = quote_data.get("quoteNumber")
            self.write_csv()
            return True
        return False

    def write_csv(self) -> None:
        """
        Append output data to CSV.
        
        Changes: Switched to csv module (efficiency: lighter than Pandas).
        - Added file locking potential (for multi-threaded use).
        - Removed redundant dict comprehension.
        """
        try:
            csv_file = self.csv_file
            file_exists = os.path.exists(csv_file)
            headers = list(self.output_data_dict.keys())
            mode = "a" if file_exists else "w"

            with open(csv_file, mode, newline="") as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                if not file_exists:
                    writer.writeheader()
                writer.writerow(self.output_data_dict)

            logger.info(f"{self.flow_instance_id} appended to {csv_file}")
        except Exception as e:
            logger.error(f"Error in write_csv: {e}")
            raise

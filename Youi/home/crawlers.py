
import asyncio
import copy
from datetime import datetime
import json
import logging
import os
import sys

import pandas as pd

from base import BaseYouiCrawler, YouiMappings
from utilities import get_headers_playwright

logger = logging.getLogger("youi_runner")



class YouiCombinedCrawler(BaseYouiCrawler):

    def __init__(self, input_data, output_file,toggle=False):
        # Session management
        self.flow_instance_id = None
        self.cookies = {}
        self.headers = {}

        # Core structures
        self.base_json_data = {}
        self.input_data = input_data
        self.csv_file = output_file

        # Mappings encapsulation
        self.mappings = YouiMappings(self.input_data)

        # Output fields
        self.output_data_dict = {
            "sample_number": self.input_data.get("SAMPLE_NUMBER"),
            "Cover": self.input_data.get("cover"),
            "DOB_day": self.input_data.get("DOB_dummy_day"),
            "DOB_month": self.input_data.get("DOB_dummy_month"),
            "DOB_year": self.input_data.get("DOB_dummy_year"),
            "Postcode": self.input_data.get("postcode"),
            "QuoteDate": datetime.now().strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": datetime.now().strftime("%H:%M"),
            "QuoteLink": "NA",
            "QuoteNumber": "NA",
            "CoreLogicEstimate":"N/A",
            "BuildingDefaultExcess": "NA",
            "ContentsDefaultExcess": "NA",
            "YearlyPayment": "NA",
            "Reason_for_quote_failure": "NA",
        }

        # Preserved for legacy reasons, may be removed after review
        self.property_address = {}
        self.message = None
        self.quote_url = "https://quote.youi.com.au/api/quote"

    async def async_init(self):
        await self._initialize_session()
        return self

    async def _initialize_session(self) -> None:
        """Initialize crawler session with proper error handling."""
        try:
            session_data = await get_headers_playwright()
            if session_data:
                self.cookies, self.headers, self.flow_instance_id = session_data
            logger.info(f"Session initialized with flow ID: {self.flow_instance_id}")
        except Exception as e:
            logger.error(f"Failed to initialize session: {e}")
            raise

    def populate_page_02(self):
        translation = self.mappings.translate_input_data_page_02()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_03(self):
        translation = self.mappings.translate_input_data_page_03()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_04(self):
        translation = self.mappings.translate_input_data_page_04()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_05(self):
        business_use_details = None
        if self.input_data.get("business_use") == 'Yes':
            business_type = self.input_data.get("nature_of_business")
            business_use_details = self.extract_business_data(business_type)

        translation = self.mappings.translate_input_data_page_05(business_use_details)
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_06(self):
        if self.property_address:
            translation = self.mappings.translate_input_data_page_06(self.property_address,None)
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def collect_premium(self):
        """
        Collects the output values from the API and writes them to CSV.
        """
        qd = self.base_json_data.get("quoteData", {})
        self.output_data_dict.update({
            "Reason_for_quote_failure": self.message,
            "QuoteLink": f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}",
            "QuoteNumber": qd.get("quoteNumber"),
            "ContentsDefaultExcess": qd.get("basicExcessContents"),
            "BuildingDefaultExcess": qd.get("basicExcessBuilding"),
            "YearlyPayment": qd.get("annualPremium"),
        })
        self.write_csv()

    def populate_cyclone_details(self):
        # This logic is not mapping-dependent; left as-is.
        self.base_json_data["quoteData"].update(
            {
                "rollerDoorType": "16621",
                "roofReplacementType": "16590",
                "windowProtectionType": "16593",
            }
        )
        qd = self.base_json_data["quoteData"]
        for k, v in {
            "rollerDoorType": "displayRollerDoorType",
            "roofReplacementType": "displayRoofReplacementType",
            "windowProtectionType": "displayWindowProtectionType"
        }.items():
            if qd.get(v) == "808":
                qd.pop(k, None)
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def switch_flow_customer_choice(self):
        self.base_json_data["quoteData"].update(
            {"switchFlowCustomerChoice": "ContinueOnline"}
        )
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data
    
class YouiBuildingCrawler(BaseYouiCrawler):

    def __init__(self, input_data, output_file,toggle=False):
        # Session management
        self.flow_instance_id = None
        self.cookies = {}
        self.headers = {}

        # Core structures
        self.base_json_data = {}
        self.input_data = input_data
        self.csv_file = output_file

        # Mappings encapsulation
        self.mappings = YouiMappings(self.input_data)

        # Output fields
        self.output_data_dict = {
            "sample_number": self.input_data.get("SAMPLE_NUMBER"),
            "Cover": self.input_data.get("cover"),
            "DOB_day": self.input_data.get("DOB_dummy_day"),
            "DOB_month": self.input_data.get("DOB_dummy_month"),
            "DOB_year": self.input_data.get("DOB_dummy_year"),
            "Postcode": self.input_data.get("postcode"),
            "QuoteDate": datetime.now().strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": datetime.now().strftime("%H:%M"),
            "QuoteLink": "N/A",
            "QuoteNumber": "N/A",
            "CoreLogicEstimate":"N/A",
            "DefaultExcess": "N/A",
            "YearlyPayment": "N/A",
            "Reason_for_quote_failure": "N/A",
        }

        self.toggle = toggle
        if self.toggle:
            self.output_data_dict = self.output_data_dict = {
            "sample_number": self.input_data.get("SAMPLE_NUMBER"),
            "Cover": self.input_data.get("cover"),
            "DOB_day": self.input_data.get("DOB_dummy_day"),
            "DOB_month": self.input_data.get("DOB_dummy_month"),
            "DOB_year": self.input_data.get("DOB_dummy_year"),
            "Postcode": self.input_data.get("postcode"),
            "QuoteDate": datetime.now().strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": datetime.now().strftime("%H:%M"),
            "QuoteLink": "N/A",
            "QuoteNumber": "N/A",
            "DefaultExcess": "N/A",
            "MonthlyPayment": "N/A",
            "YearlyPayment": "N/A",
            "BuildingsAccidentalLossOrDamage": "N/A",
            "excess_300": "N/A",
            "excess_350": "N/A",
            "excess_400": "N/A",
            "excess_450": "N/A",
            "excess_500": "N/A",
            "excess_550": "N/A",
            "excess_600": "N/A",
            "excess_650": "N/A",
            "excess_700": "N/A",
            "excess_750": "N/A",
            "excess_800": "N/A",
            "excess_850": "N/A",
            "excess_900": "N/A",
            "excess_950": "N/A",
            "excess_1000": "N/A",
            "excess_1050": "N/A",
            "excess_1100": "N/A",
            "excess_1150": "N/A",
            "excess_1200": "N/A",
            "excess_1250": "N/A",
            "excess_1300": "N/A",
            "excess_1350": "N/A",
            "excess_1400": "N/A",
            "excess_1450": "N/A",
            "excess_1500": "N/A",
            "excess_1550": "N/A",
            "excess_1600": "N/A",
            "excess_1650": "N/A",
            "excess_1700": "N/A",
            "excess_1750": "N/A",
            "excess_1800": "N/A",
            "excess_1850": "N/A",
            "excess_1900": "N/A",
            "excess_1950": "N/A",
            "excess_2000": "N/A",
            "excess_2050": "N/A",
            "excess_2100": "N/A",
            "excess_2150": "N/A",
            "excess_2200": "N/A",
            "excess_2250": "N/A",
            "excess_2300": "N/A",
            "excess_2350": "N/A",
            "excess_2400": "N/A",
            "excess_2450": "N/A",
            "excess_2500": "N/A",
            "excess_2550": "N/A",
            "excess_2600": "N/A",
            "excess_2650": "N/A",
            "excess_2700": "N/A",
            "excess_2750": "N/A",
            "excess_2800": "N/A",
            "excess_2850": "N/A",
            "excess_2900": "N/A",
            "excess_2950": "N/A",
            "excess_3000": "N/A",
            "Reason_for_quote_failure": "N/A",
        }


        # Preserved for legacy reasons, may be removed after review
        self.property_address = {}
        self.message = None
        self.quote_url = "https://quote.youi.com.au/api/quote"



    async def async_init(self):
        await self._initialize_session()
        return self

    async def _initialize_session(self) -> None:
        """Initialize crawler session with proper error handling."""
        try:
            session_data = await get_headers_playwright()
            if session_data:
                self.cookies, self.headers, self.flow_instance_id = session_data
            logger.info(f"Session initialized with flow ID: {self.flow_instance_id}")
        except Exception as e:
            logger.error(f"Failed to initialize session: {e}")
            raise

    def populate_page_02(self):
        translation = self.mappings.translate_input_data_page_02()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_03(self):
        translation = self.mappings.translate_input_data_page_03()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_04(self):
        translation = self.mappings.translate_input_data_page_04()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_06(self):
        business_use_details = None
        if self.input_data.get("business_use") == 'Yes':
            business_type = self.input_data.get("nature_of_business")
            business_use_details = self.extract_business_data(business_type)

        if self.property_address:
            translation = self.mappings.translate_input_data_page_06(self.property_address,business_use_details)

        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def collect_premium(self) -> None:
        """
        Collect premium data from base JSON and update output dict.
        If self.toggle is True, collect additional toggled fields (accidental damage, monthly, excesses).
        Finally, write to CSV.
        
        Changes:
        - Consolidated main and toggle logic into one method (DRY: reduces duplication).
        - Removed redundant assignments (efficiency).
        - Added safe gets with defaults and error handling for API calls (robustness).
        - Assumed self.toggle is a boolean; added check/logging if unset.
        - Added docstring and type hints (readability).
        """
        if not hasattr(self, 'toggle'):
            logger.warning("self.toggle not set; defaulting to False")
            self.toggle = False  # Or raise ValueError if required

        quote_data = self.base_json_data.get("quoteData", {})

        # Common fields (DRY: define once)
        common_data = {
            "Reason_for_quote_failure": self.message or "N/A",  # Safe default
            "QuoteLink": f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}",
            "QuoteNumber": quote_data.get("quoteNumber", "N/A"),
            "DefaultExcess": quote_data.get("basicExcessBuilding", "N/A"),
            "YearlyPayment": quote_data.get("annualPremium", "N/A"),
        }
        self.output_data_dict.update(common_data)

        if self.toggle:
            try:
                # Accidental damage toggle
                accidental_damage_payload = copy.deepcopy(self.base_json_data)
                accidental_damage_payload["quoteData"]["selectedOptionalExtrasBuilding"] = ["ABB"]
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=accidental_damage_payload,
                )
                self.output_data_dict["BuildingsAccidentalLossOrDamage"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect accidental damage premium: {e}")
                self.output_data_dict["BuildingsAccidentalLossOrDamage"] = "Error"

            try:
                # Monthly payment toggle
                monthly_premium_payload = copy.deepcopy(self.base_json_data)
                monthly_premium_payload["quoteData"]["paymentFrequency"] = "M"
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=monthly_premium_payload,
                )
                self.output_data_dict["MonthlyPayment"] = response.get("monthlyPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect monthly premium: {e}")
                self.output_data_dict["MonthlyPayment"] = "Error"

            try:
                # Excess toggles (loop over available excesses)
                excess_payload = copy.deepcopy(self.base_json_data)
                available_excesses = quote_data.get("availableBasicExcessesBuilding", [])
                for excess_value in available_excesses:
                    excess_payload["quoteData"]["basicExcessBuilding"] = excess_value
                    response = self.make_request(
                        "https://quote.youi.com.au/api/quote/premium",
                        method="POST",
                        payload=excess_payload,
                    )
                    self.output_data_dict[f"excess_{int(excess_value)}"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect excess premiums: {e}")
                # Set defaults for all to avoid partial data
                for excess_value in available_excesses:
                    self.output_data_dict[f"excess_{int(excess_value)}"] = "Error"

        self.write_csv()

    def populate_cyclone_details(self):
        # This logic is not mapping-dependent; left as-is.
        self.base_json_data["quoteData"].update(
            {
                "rollerDoorType": "16621",
                "roofReplacementType": "16590",
                "windowProtectionType": "16593",
            }
        )
        qd = self.base_json_data["quoteData"]
        for k, v in {
            "rollerDoorType": "displayRollerDoorType",
            "roofReplacementType": "displayRoofReplacementType",
            "windowProtectionType": "displayWindowProtectionType"
        }.items():
            if qd.get(v) == "808":
                qd.pop(k, None)
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def switch_flow_customer_choice(self):
        self.base_json_data["quoteData"].update(
            {"switchFlowCustomerChoice": "ContinueOnline"}
        )
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data
    

class YouiContentsCrawler(BaseYouiCrawler):

    def __init__(self, input_data, output_file,toggle=False):
        # Session management
        self.flow_instance_id = None
        self.cookies = {}
        self.headers = {}

        # Core structures
        self.base_json_data = {}
        self.input_data = input_data
        self.csv_file = output_file

        # Mappings encapsulation
        self.mappings = YouiMappings(self.input_data)

        # Output fields
        self.output_data_dict = {
            "sample_number": self.input_data.get("SAMPLE_NUMBER"),
            "Cover": self.input_data.get("cover"),
            "DOB_day": self.input_data.get("DOB_dummy_day"),
            "DOB_month": self.input_data.get("DOB_dummy_month"),
            "DOB_year": self.input_data.get("DOB_dummy_year"),
            "Postcode": self.input_data.get("postcode"),
            "QuoteDate": datetime.now().strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": datetime.now().strftime("%H:%M"),
            "QuoteLink": "NA",
            "QuoteNumber": "NA",
            "CoreLogicEstimate":"N/A",
            "DefaultExcess": "NA",
            "YearlyPayment": "NA",
            "Reason_for_quote_failure": "NA",
        }

        self.toggle = toggle
        if self.toggle:
            self.output_data_dict = self.output_data_dict = {
            "sample_number": self.input_data.get("SAMPLE_NUMBER"),
            "Cover": self.input_data.get("cover"),
            "DOB_day": self.input_data.get("DOB_dummy_day"),
            "DOB_month": self.input_data.get("DOB_dummy_month"),
            "DOB_year": self.input_data.get("DOB_dummy_year"),
            "Postcode": self.input_data.get("postcode"),
            "QuoteDate": datetime.now().strftime("%d-%b-%y"),
            "Timestamp (in hh:mm)": datetime.now().strftime("%H:%M"),
            "QuoteLink": "N/A",
            "QuoteNumber": "N/A",
            "DefaultExcess": "N/A",
            "MonthlyPayment": "N/A",
            "YearlyPayment": "N/A",
            "BuildingsAccidentalLossOrDamage": "N/A",
            "excess_300": "N/A",
            "excess_350": "N/A",
            "excess_400": "N/A",
            "excess_450": "N/A",
            "excess_500": "N/A",
            "excess_550": "N/A",
            "excess_600": "N/A",
            "excess_650": "N/A",
            "excess_700": "N/A",
            "excess_750": "N/A",
            "excess_800": "N/A",
            "excess_850": "N/A",
            "excess_900": "N/A",
            "excess_950": "N/A",
            "excess_1000": "N/A",
            "excess_1050": "N/A",
            "excess_1100": "N/A",
            "excess_1150": "N/A",
            "excess_1200": "N/A",
            "excess_1250": "N/A",
            "excess_1300": "N/A",
            "excess_1350": "N/A",
            "excess_1400": "N/A",
            "excess_1450": "N/A",
            "excess_1500": "N/A",
            "excess_1550": "N/A",
            "excess_1600": "N/A",
            "excess_1650": "N/A",
            "excess_1700": "N/A",
            "excess_1750": "N/A",
            "excess_1800": "N/A",
            "excess_1850": "N/A",
            "excess_1900": "N/A",
            "excess_1950": "N/A",
            "excess_2000": "N/A",
            "excess_2050": "N/A",
            "excess_2100": "N/A",
            "excess_2150": "N/A",
            "excess_2200": "N/A",
            "excess_2250": "N/A",
            "excess_2300": "N/A",
            "excess_2350": "N/A",
            "excess_2400": "N/A",
            "excess_2450": "N/A",
            "excess_2500": "N/A",
            "excess_2550": "N/A",
            "excess_2600": "N/A",
            "excess_2650": "N/A",
            "excess_2700": "N/A",
            "excess_2750": "N/A",
            "excess_2800": "N/A",
            "excess_2850": "N/A",
            "excess_2900": "N/A",
            "excess_2950": "N/A",
            "excess_3000": "N/A",
            "Reason_for_quote_failure": "N/A",
        }

        # Preserved for legacy reasons, may be removed after review
        self.property_address = {}
        self.message = None
        self.quote_url = "https://quote.youi.com.au/api/quote"

    async def async_init(self):
        await self._initialize_session()
        return self

    async def _initialize_session(self) -> None:
        """Initialize crawler session with proper error handling."""
        try:
            session_data = await get_headers_playwright()
            if session_data:
                self.cookies, self.headers, self.flow_instance_id = session_data
            logger.info(f"Session initialized with flow ID: {self.flow_instance_id}")
        except Exception as e:
            logger.error(f"Failed to initialize session: {e}")
            raise

    def populate_page_02(self):
        translation = self.mappings.translate_input_data_page_02()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_03(self):
        translation = self.mappings.translate_input_data_page_03()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_04(self):
        translation = self.mappings.translate_input_data_page_04()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_05(self):
        business_use_details = None
        if self.input_data.get("business_use") == 'Yes':
            business_type = self.input_data.get("nature_of_business")
            business_use_details = self.extract_business_data(business_type)

        translation = self.mappings.translate_input_data_page_05(business_use_details)
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def populate_page_06(self):
        if self.property_address:
            translation = self.mappings.translate_input_data_page_06(self.property_address,None)
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def collect_premium(self) -> None:
        """
        Collect contents premium data from base JSON and update output dict.
        If self.toggle is True, collect additional toggled fields (accidental damage, business items,
        commercial storage, monthly, excesses) via API calls and log optional extras.
        Finally, write to CSV.
        
        Changes:
        - Consolidated main and toggle logic into one method (DRY: reduces duplication).
        - Removed redundant assignments and commented code (efficiency: clean up deprecated logic).
        - Added safe gets with defaults and error handling for API calls (robustness).
        - Assumed self.toggle is a boolean; added check/logging if unset.
        - Retained extras loop for logging (as in original); expand if collection needed.
        - Added docstring and type hints (readability).
        """
        if not hasattr(self, 'toggle'):
            logger.warning("self.toggle not set; defaulting to False")
            self.toggle = False  # Or raise ValueError if required

        quote_data = self.base_json_data.get("quoteData", {})

        # Common fields (DRY: define once)
        common_data = {
            "Reason_for_quote_failure": self.message or "N/A",  # Safe default
            "QuoteLink": f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}",
            "QuoteNumber": quote_data.get("quoteNumber", "N/A"),
            "DefaultExcess": quote_data.get("basicExcessContents", "N/A"),
            "YearlyPayment": quote_data.get("annualPremium", "N/A"),
        }
        self.output_data_dict.update(common_data)

        if self.toggle:
            try:
                # Log optional extras (as in original; expand to collect if needed)
                available_extras = quote_data.get("availableOptionalExtrasContents", [])
                for extra in available_extras:
                    if extra.get("key") == "ALD":
                        continue
                    logger.info(f"Optional extra: {extra}")
            except Exception as e:
                logger.error(f"Failed to process optional extras: {e}")

            try:
                # Accidental damage toggle ("ALD")
                accidental_damage_payload = copy.deepcopy(self.base_json_data)
                accidental_damage_payload["quoteData"]["selectedOptionalExtrasContents"] = ["ALD"]
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=accidental_damage_payload,
                )
                self.output_data_dict["ContentsAccidentalLossOrDamage"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect accidental damage premium: {e}")
                self.output_data_dict["ContentsAccidentalLossOrDamage"] = "Error"

            try:
                # Business items toggle ("CTT")
                contents_business_payload = copy.deepcopy(self.base_json_data)
                contents_business_payload["quoteData"]["selectedOptionalExtrasContents"] = ["CTT"]
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=contents_business_payload,
                )
                self.output_data_dict["ContentsBusinessItems"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect business items premium: {e}")
                self.output_data_dict["ContentsBusinessItems"] = "Error"

            try:
                # Commercial storage toggle ("CIS")
                commercial_storage_payload = copy.deepcopy(self.base_json_data)
                commercial_storage_payload["quoteData"]["selectedOptionalExtrasContents"] = ["CIS"]
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=commercial_storage_payload,
                )
                self.output_data_dict["ContentsInCommercialStorage"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect commercial storage premium: {e}")
                self.output_data_dict["ContentsInCommercialStorage"] = "Error"

            try:
                # Monthly payment toggle
                monthly_premium_payload = copy.deepcopy(self.base_json_data)
                monthly_premium_payload["quoteData"]["paymentFrequency"] = "M"
                response = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=monthly_premium_payload,
                )
                self.output_data_dict["MonthlyPayment"] = response.get("monthlyPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect monthly premium: {e}")
                self.output_data_dict["MonthlyPayment"] = "Error"

            try:
                # Excess toggles (loop over available excesses)
                excess_payload = copy.deepcopy(self.base_json_data)
                available_excesses = quote_data.get("availableBasicExcessesContents", [])
                for excess_value in available_excesses:
                    excess_payload["quoteData"]["basicExcessContents"] = excess_value
                    response = self.make_request(
                        "https://quote.youi.com.au/api/quote/premium",
                        method="POST",
                        payload=excess_payload,
                    )
                    self.output_data_dict[f"excess_{int(excess_value)}"] = response.get("annualPremium", "N/A")
            except Exception as e:
                logger.error(f"Failed to collect excess premiums: {e}")
                # Set defaults for all to avoid partial data
                for excess_value in available_excesses:
                    self.output_data_dict[f"excess_{int(excess_value)}"] = "Error"

        self.write_csv()

    def populate_cyclone_details(self):
        # This logic is not mapping-dependent; left as-is.
        self.base_json_data["quoteData"].update(
            {
                "rollerDoorType": "16621",
                "roofReplacementType": "16590",
                "windowProtectionType": "16593",
            }
        )
        qd = self.base_json_data["quoteData"]
        for k, v in {
            "rollerDoorType": "displayRollerDoorType",
            "roofReplacementType": "displayRoofReplacementType",
            "windowProtectionType": "displayWindowProtectionType"
        }.items():
            if qd.get(v) == "808":
                qd.pop(k, None)
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def switch_flow_customer_choice(self):
        self.base_json_data["quoteData"].update(
            {"switchFlowCustomerChoice": "ContinueOnline"}
        )
        response_json_data = self.make_request(
            self.quote_url,
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data
  
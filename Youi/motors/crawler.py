
import asyncio
import copy
from datetime import datetime, timezone
import json
import logging
import os
import sys
from zoneinfo import ZoneInfo

# Automatically handles GMT/BST switching
uk_tz = ZoneInfo("Europe/London")

import pandas as pd

from base import BaseYouiCrawler, YouiMappings
from utilities import get_headers_playwright

logger = logging.getLogger("youi_runner")


class YouiMotors(BaseYouiCrawler):

    def __init__(self, input_data, output_file,toggle=False):
        # Session management
        self.flow_instance_id = None
        self.cookies = {}
        self.headers = {}

        # Core structures
        self.base_json_data = {}
        self.input_data = input_data
        self.csv_file = output_file

        self.deduced_car = ''

        # Mappings encapsulation
        self.mappings = YouiMappings(self.input_data)
        aest_tz = ZoneInfo("Australia/Sydney")
        now = datetime.now(aest_tz)
        
        # Output fields
        self.output_data_dict = {
            "SAMPLE_NUMBER":self.input_data.get("SAMPLE_NUMBER"),
            "Postcode": self.input_data.get("POSTCODE"),
            "DOB_day": self.input_data.get("DOB_DUMMY_DAY"),
            "DOB_month": self.input_data.get("DOB_DUMMY_MONTH"),
            "DOB_year": self.input_data.get("DOB_DUMMY_YEAR"),
            "QuoteDate": now.strftime("%d-%b-%y"),
            "Timestamp AEST (in hh:mm)": now.strftime("%H:%M"),
            "DeducedVehicleSelection": "",
            "QuoteLink": "",
            "QuoteNumber": "",
            "DefaultExcess": "",
            "MonthlyPayment": "",
            "YearlyPayment": "",
            "RoadsideAssist": "",
            "HireCar": "",
            "WindowGlassExcess": "",
            "BusinessItemsCover": "",
            "AgreedValue": "",
            "MiddleAgreedValueSI": "",
            "excess500": "",
            "excess550": "",
            "excess600": "",
            "excess650": "",
            "excess700": "",
            "excess750": "",
            "excess800": "",
            "excess850": "",
            "excess900": "",
            "excess950": "",
            "excess1000": "",
            "excess1050": "",
            "excess1100": "",
            "excess1150": "",
            "excess1200": "",
            "excess1250": "",
            "excess1300": "",
            "excess1350": "",
            "excess1400": "",
            "excess1450": "",
            "excess1500": "",
            "excess1550": "",
            "excess1600": "",
            "excess1650": "",
            "excess1700": "",
            "excess1750": "",
            "excess1800": "",
            "excess1850": "",
            "excess1900": "",
            "excess1950": "",
            "excess2000": "",
            "excess2050": "",
            "excess2100": "",
            "excess2150": "",
            "excess2200": "",
            "excess2250": "",
            "excess2300": "",
            "excess2350": "",
            "excess2400": "",
            "excess2450": "",
            "excess2500": "",
            "Reason_for_quote_failure": "",
            "Remark (if any)": "",
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

    def populate_page_01(self):

        progress_dict = {}
        
        progress_dict = self.build_car_information(self.input_data)
        print(progress_dict)
        self.deduced_car = f"{progress_dict['car_make_name']} | {progress_dict['car_model_key']} | {progress_dict['car_make_year']} | {progress_dict['car_badge']} | {progress_dict['car_style']} | {progress_dict['car_description']} | {progress_dict['colour']}"
        translation = self.mappings.translate_input_data_page_01(progress_dict)
        self.base_json_data["quoteData"].update(translation)
        
        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_02(self):

        translation = self.mappings.translate_input_data_page_02()

        self.base_json_data["quoteData"].update(translation)
        
        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_03(self):

        business_usage = None
        if self.input_data.get("PG_VEHICLEUSE1").title() == 'Business':
            business_usage = self.buisness_usage(self.input_data.get("PG_VEHICLEUSE2"))

        translation = self.mappings.translate_input_date_page_03(business_usage)

        self.base_json_data["quoteData"].update(translation)
        
        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_04(self):
        self.property_address = self.get_address_by_suburb()
        if self.property_address:
            translation = self.mappings.translate_input_date_page_04(self.property_address)

        self.base_json_data["quoteData"].update(translation)
        print(self.base_json_data)
        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_05(self):
        translation = self.mappings.translate_input_data_page_05()

        self.base_json_data["quoteData"].update(translation)
        
        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_06(self):
        translation = self.mappings.translate_input_data_page_06()

        self.base_json_data["quoteData"]["people"][0].update(translation)

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_07(self):
        translation = self.mappings.translate_input_data_page_07()

        self.base_json_data["quoteData"]["people"][0].update(translation)

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_08(self):
        translation = self.mappings.translate_input_data_page_08()

        self.base_json_data["quoteData"]["people"][0].update(translation)

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_09(self):
        translation = self.mappings.translate_input_data_page_09()

        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_10(self):
        translation = self.mappings.translation_input_data_page_10()

        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    
    def populate_page_11(self):
        translation = self.mappings.translation_input_data_page_11(self.property_address)

        self.base_json_data["quoteData"]["people"][0]["licenceDetails"].update(
            translation['licenceDetails']
        )

        self.base_json_data["quoteData"]["people"][0].update(
            translation['people']
        )

        new_translation = {k:v for k,v in translation.items() if k not in ['people','licenceDetails']}

        self.base_json_data["quoteData"].update(
            new_translation
        )


        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)

        if self.input_data.get("SECOND_DRIVER_FLAG") == 'YES':
            second_driver_translation = self.mappings.translation_input_data_page_12(self.property_address)

            self.base_json_data['quoteData'].update({'addListedDriver':'808'})
            self.base_json_data["quoteData"]["people"].append(second_driver_translation)

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote",
                method="PUT",
                payload=self.base_json_data,
            )
            self.base_json_data = self.update_json(response_json_data)

            drivers = self.base_json_data["quoteData"]["people"]
            person_key = None
            for driver in drivers:
                first_name = driver.get('firstName')
                surname = driver.get('surname')
                person_key = driver.get('personKey')
                if f"{first_name} {surname}".lower() == self.input_data.get("FULL_NAME_DUMMY").lower():
                    break 

            self.base_json_data["quoteData"].update({'vehicleRegularDriverKey':person_key})

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote",
                method="PUT",
                payload=self.base_json_data,
            )
            self.base_json_data = self.update_json(response_json_data)


        return self.base_json_data
    
    def populate_page_12(self):
        translation = self.mappings.translation_input_data_page_13()
        self.base_json_data["quoteData"].update(translation)

        response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote",
                method="PUT",
                payload=self.base_json_data,
            )
        self.base_json_data = self.update_json(response_json_data)

        return self.base_json_data
    

    def small_business_usage(self):

        self.base_json_data["quoteData"].update({"smallBusinessUsage": "808"})

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote",
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
            "https://quote.youi.com.au/api/quote",
            method="PUT",
            payload=self.base_json_data,
        )
        self.base_json_data = self.update_json(response_json_data)
        return self.base_json_data

    def collect_window_glass_excess(self):

        cleaned_payload = copy.deepcopy(self.base_json_data)
        cleaned_payload["quoteData"]["hasWindscreenExcess"] = True

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote/premium",
            method="POST",
            payload=cleaned_payload,
        )
        self.output_data_dict["WindowGlassExcess"]= response_json_data.get("annualPremium")

    def collect_monthly_excess(self):

        cleaned_payload = copy.deepcopy(self.base_json_data)
        cleaned_payload["quoteData"]["paymentFrequency"] = "M"

        response_json_data = self.make_request(
            "https://quote.youi.com.au/api/quote/premium",
            method="POST",
            payload=cleaned_payload,
        )
        self.output_data_dict["MonthlyPayment"]= response_json_data.get("monthlyPremium")

    def choice_of_repair(self):
        try:
            # get choice of repaire
            cleaned_payload = copy.deepcopy(self.base_json_data)
            cleaned_payload["quoteData"]["selectedOptionalExtras"] = ["COR"]

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote/premium",
                method="POST",
                payload=cleaned_payload,
            )

            self.output_data_dict["ChoiceOfRepairer"] = response_json_data.get("annualPremium")

        except Exception as e:
            print("error in choice_of_repair -->", e)
            raise e  # Raise the exception to skip the iteration

    def business_items_cover(self):
        try:
            # get BusinessItemsCover
            cleaned_payload = copy.deepcopy(self.base_json_data)
            cleaned_payload["quoteData"]["selectedOptionalExtras"] = ["TOT"]

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote/premium",
                method="POST",
                payload=cleaned_payload,
            )

            self.output_data_dict["BusinessItemsCover"] = response_json_data.get("annualPremium")

        except Exception as e:
            print("error in business_items_cover -->", e)
            raise e  # Raise the exception to skip the iteration

    def get_all_excess(self):
        try:
            cleaned_payload = copy.deepcopy(self.base_json_data)
            all_excess = cleaned_payload.get("quoteData",{}).get("availableBasicExcesses")

            for excess in all_excess:
                excess = int(excess)
                cleaned_payload["quoteData"]["basicExcess"] = excess

                response_json_data = self.make_request(
                    "https://quote.youi.com.au/api/quote/premium",
                    method="POST",
                    payload=cleaned_payload,
                )

                self.output_data_dict[f"excess{excess}"] = response_json_data.get("annualPremium")

        except Exception as e:
            print("error in get_all_excess -->", e)
            raise e  # Raise the exception to skip the iteration

    def hire_car(self):
        try:
            # get HireCar
            cleaned_payload = copy.deepcopy(self.base_json_data)
            cleaned_payload["quoteData"]["selectedOptionalExtras"] = ["CHO"]

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote/premium",
                method="POST",
                payload=cleaned_payload,
            )

            self.output_data_dict["HireCar"] = response_json_data.get("annualPremium")
        except Exception as e:
            print("error in hire_car -->", e)
            raise e  # Raise the exception to skip the iteration

    def agreed_value(self,MiddleAgreedValueSI):
        try:
            cleaned_payload = copy.deepcopy(self.base_json_data)

            # to set agreed value
            cleaned_payload["quoteData"]["insuredValueType"] = "6343"
            cleaned_payload["quoteData"]["insuredValue"] = MiddleAgreedValueSI
            cleaned_payload["quoteData"]["hasInsuredValueTypeChanged"] = True

            response_json_data = self.make_request(
                "https://quote.youi.com.au/api/quote/premium",
                method="POST",
                payload=cleaned_payload,
            )

            self.output_data_dict["AgreedValue"] = response_json_data.get("annualPremium")

        except Exception as e:
            print("error in agreed_value -->", e)
            raise e  # Raise the exception to skip the iteration

    def collect_premium(self):

        self.output_data_dict['QuoteNumber'] = self.base_json_data.get("quoteData",{}).get("quoteNumber")
        self.output_data_dict['QuoteLink'] = f"https://quote.youi.com.au/quote/online/{self.flow_instance_id}"
        self.output_data_dict["DefaultExcess"] = self.base_json_data.get("quoteData",{}).get("basicExcess")
        self.output_data_dict["YearlyPayment"]= self.base_json_data.get("quoteData",{}).get("annualPremium")
        self.output_data_dict["DeducedVehicleSelection"]= self.deduced_car

        minimumModelValue = int(self.base_json_data["quoteData"].get("minimumModelValue", 0))
        maximumModelValue = int(self.base_json_data["quoteData"].get("maximumModelValue", 0))        
        MiddleAgreedValueSI = (maximumModelValue + minimumModelValue) / 2
        self.output_data_dict["MiddleAgreedValueSI"] = MiddleAgreedValueSI

        self.collect_monthly_excess()
        self.choice_of_repair()
        self.business_items_cover()
        self.hire_car()
        self.agreed_value(MiddleAgreedValueSI)
        self.get_all_excess()
        self.collect_window_glass_excess()

        self.write_csv()

        # self.output_data_dict["RoadsideAssist"]= self.base_json_data.get("quoteData",{}).get("basicExcess")

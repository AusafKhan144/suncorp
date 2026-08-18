# %%
import requests
import json

url = "https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote"

payload = json.dumps({})
headers = {
  'accept-language': 'en-US,en;q=0.9',
  'content-type': 'application/json',
  'origin': 'https://insurance.nrma.com.au',
  'sec-fetch-site': 'same-origin',
  'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',

}

response = requests.request("POST", url, headers=headers, data=payload)
jo = response.json()

url = "https://connect.nrma.com.au/cb/anonymous/quote/nrma"

quote_id = jo.get("quoteId")
quote_id_signature_expiry = jo.get("quoteIdSignatureExpiry")
quote_id_signature = jo.get("quoteIdSignature")

payload = json.dumps({
  "payload": {
    "quoteId": quote_id,
    "expiry": quote_id_signature_expiry
  },
  "signature": quote_id_signature
})

response = requests.request("POST", url, headers=headers, data=payload)
cookies = {'x-iag-customer': response.json().get("accessToken"),}

url = "https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/reference/address/lookup/"

payload = json.dumps({
  "excludePostalAddresses": True,
  "address": "10 CHUTER ST, CASEY ACT 2913",
  "brand": "nrma"
})

response = requests.request("POST", url, headers=headers, data=payload, cookies=cookies)

display_address_line1 = response.json()[0].get("display_address_line1")
state = response.json()[0].get("state")
postcode = response.json()[0].get("postcode")
locality_name = response.json()[0].get("locality_name")
address = response.json()[0]

json_data = {
    'address': {
        'descriptive_address_line1': display_address_line1,
        'state': state,
        'locality_name': locality_name,
        'postcode': postcode,
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/reference/property/{quote_id}',
    cookies=cookies,
    headers=headers,
    json=json_data,
)
json_data = {
    'quoteType': 'QUICK',
    'answers': {
        'BillingFrequency': 'MONTHLY',
        'PayDirectDebit': [
            'true',
        ],
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': address,
        'PropertyManualAddress': None,
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}',
    cookies=cookies,
    headers=headers,
    json=json_data,
)
building_public_id = response.json().get("buildingPublicId")
asset_public_id = response.json().get("assetPublicId")

json_data = {
    'quoteType': 'QUICK',
    'answers': {
        'BillingFrequency': 'MONTHLY',
        'PayDirectDebit': [
            'true',
        ],
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': address,
        'PropertyManualAddress': None,
        'SmallBedrooms': 1,
        'MediumBedrooms': 3,
        'LargeBedrooms': 1,
        'Bathrooms': 3,
        'ParkingSpacesCarport': 0,
        'ParkingSpacesGarage': 1,
        'PropertyType': 'FREESTANDING_HOME',
        'YearConstructed': '2012',
        'RoofMaterial': 'CONCRETE_TILE',
        'WallMaterial': 'BRICK_VENEER',
        'LandSlope': 'FLAT_GENTLE',
        'Storeys': '2',
        'BuiltInHeatingOrCooling': False,
        'InGroundPool': False,
        'TennisCourt': False,
        'SolarPanels': False,
        'QualityOfFixtures': 'STANDARD',
        'Verandahs': 1,
        'BalconiesAndDecks': 1,
        'SmallSheds': 0,
        'LargeSheds': 0,
        'FloodingRisk': False,
        'JettyWharfLandingPier': False,
        'BodyCorporateOrStrata': False,
        'AssetPublicId': asset_public_id,
        'BuildingPublicId': building_public_id,
        'LivingSpaces': 1,
        'DiningRooms': 1,
        'ElectricalWiringReplaced': False,
        'PropertyOccupancyType': 'OWN_AND_LIVE_IN_HOME',
        'ChildOccupants': 0,
        'UsageArrangement': 'OWN_AND_LIVE_IN_HOME',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

json_data = {
    'quoteType': 'QUICK',
    'answers': {
        'BillingFrequency': 'MONTHLY',
        'PayDirectDebit': [
            'true',
        ],
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': address,
        'PropertyManualAddress': None,
        'SmallBedrooms': 1,
        'MediumBedrooms': 3,
        'LargeBedrooms': 1,
        'Bathrooms': 3,
        'ParkingSpacesCarport': 0,
        'ParkingSpacesGarage': 1,
        'PropertyType': 'FREESTANDING_HOME',
        'YearConstructed': '2012',
        'RoofMaterial': 'CONCRETE_TILE',
        'WallMaterial': 'BRICK_VENEER',
        'LandSlope': 'FLAT_GENTLE',
        'Storeys': '2',
        'BuiltInHeatingOrCooling': False,
        'InGroundPool': False,
        'TennisCourt': False,
        'SolarPanels': False,
        'QualityOfFixtures': 'STANDARD',
        'Verandahs': 1,
        'BalconiesAndDecks': 1,
        'SmallSheds': 0,
        'LargeSheds': 0,
        'FloodingRisk': False,
        'JettyWharfLandingPier': False,
        'BodyCorporateOrStrata': False,
        'SumInsuredHomeCustomAmount': '966343',
        'AssetPublicId': asset_public_id,
        'BuildingPublicId': building_public_id,
        'LivingSpaces': 1,
        'DiningRooms': 1,
        'ElectricalWiringReplaced': False,
        'PropertyOccupancyType': 'OWN_AND_LIVE_IN_HOME',
        'ChildOccupants': 0,
        'UsageArrangement': 'OWN_AND_LIVE_IN_HOME',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

print(response.text)

# %%
json_data = {
    'quoteType': 'QUICK',
    'answers': {
        'BillingFrequency': 'MONTHLY',
        'PayDirectDebit': [
            'true',
        ],
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': address,
        'PropertyManualAddress': None,
        'SmallBedrooms': 1,
        'MediumBedrooms': 3,
        'LargeBedrooms': 1,
        'Bathrooms': 3,
        'ParkingSpacesCarport': 0,
        'ParkingSpacesGarage': 1,
        'PropertyType': 'FREESTANDING_HOME',
        'YearConstructed': '2012',
        'RoofMaterial': 'CONCRETE_TILE',
        'WallMaterial': 'BRICK_VENEER',
        'LandSlope': 'FLAT_GENTLE',
        'Storeys': '2',
        'BuiltInHeatingOrCooling': False,
        'InGroundPool': False,
        'TennisCourt': False,
        'SolarPanels': False,
        'QualityOfFixtures': 'STANDARD',
        'Verandahs': 1,
        'BalconiesAndDecks': 1,
        'SmallSheds': 0,
        'LargeSheds': 0,
        'FloodingRisk': False,
        'JettyWharfLandingPier': False,
        'BodyCorporateOrStrata': False,
        'SumInsuredHomeCustomAmount': '966343',
        'AssetPublicId': asset_public_id,
        'BuildingPublicId': building_public_id,
        'Parties': [
            {
                'IsCustomer': False,
                'PartyGender': 'FEMALE',
                'PartyDOB': '1982-11-05',
                'PartyType': 'Individual',
                'BackendPartyRole': 'PRIMARY_POLICY_HOLDER',
            },
        ],
        'LivingSpaces': 1,
        'DiningRooms': 1,
        'ElectricalWiringReplaced': False,
        'PropertyOccupancyType': 'OWN_AND_LIVE_IN_HOME',
        'ChildOccupants': 0,
        'UsageArrangement': 'OWN_AND_LIVE_IN_HOME',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}',
    cookies=cookies,
    headers=headers,
    json=json_data,
)
response.text

# %%
asset_public_id


# %%
json_data = {
    'answers': {
        'BillingFrequency': 'MONTHLY',
        'PayDirectDebit': [
            'true',
        ],
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': address,
        'PropertyManualAddress': None,
        'SmallBedrooms': 1,
        'MediumBedrooms': 3,
        'LargeBedrooms': 1,
        'Bathrooms': 3,
        'ParkingSpacesCarport': 0,
        'ParkingSpacesGarage': 1,
        'PropertyType': 'FREESTANDING_HOME',
        'YearConstructed': '2012',
        'RoofMaterial': 'CONCRETE_TILE',
        'WallMaterial': 'BRICK_VENEER',
        'LandSlope': 'FLAT_GENTLE',
        'Storeys': '2',
        'BuiltInHeatingOrCooling': False,
        'InGroundPool': False,
        'TennisCourt': False,
        'SolarPanels': False,
        'QualityOfFixtures': 'STANDARD',
        'Verandahs': 1,
        'BalconiesAndDecks': 1,
        'SmallSheds': 0,
        'LargeSheds': 0,
        'FloodingRisk': False,
        'JettyWharfLandingPier': False,
        'BodyCorporateOrStrata': False,
        'SumInsuredHomeCustomAmount': 966343,
        'AssetPublicId': asset_public_id,
        'BuildingPublicId': building_public_id,
        'Parties': [
            {
                'IsCustomer': False,
                'PartyGender': 'FEMALE',
                'PartyDOB': '1982-11-05',
                'PartyType': 'Individual',
                'BackendPartyRole': 'PRIMARY_POLICY_HOLDER',
                'PartyId': 'ANON000000049139880',
                'HowManyPriorClaims': '0',
            },
        ],
        'LivingSpaces': 1,
        'DiningRooms': 1,
        'ElectricalWiringReplaced': False,
        'PropertyOccupancyType': 'OWN_AND_LIVE_IN_HOME',
        'ChildOccupants': 0,
        'UsageArrangement': 'OWN_AND_LIVE_IN_HOME',
        'PriorClaims': [],
        'ClaimsMade': False,
    },
    'quoteType': 'QUICK',
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}/offer',
    cookies=cookies,
    headers=headers,
    json=json_data,
)


json_data = {
    'quoteType': 'QUICK',
    'answers': {
        'LevelOfCover': 'STANDARD',
        'HowToSendPolicyDocuments': 'email_address',
        'BuildingOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'BuildingOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'ContentsOptionalExtrasAccidentalDamage': [
            'false',
        ],
        'ContentsOptionalExtrasElectricalBurnOut': [
            'false',
        ],
        'TermsConditionAgreement': True,
        'SubProduct': 'HOME',
        'CoverStartDate': '2026-07-01',
        'PropertyAddressLookup': {
            'geoLocation': {
                'gnafPID': 'GAACT717687839',
                'externalGeoId': 'AGAACT717687839',
                'confidence': 2,
                'reliability': 2,
                'levelType': 7,
                'latitude': -35.17771007,
                'longitude': 149.08894569,
            },
            'dpid': '71781224',
            'country': 'AUS',
            'display_address_line1': '10 CHUTER ST',
            'display_address_line2': 'CASEY ACT 2913',
            'full_address': '10 CHUTER ST, CASEY ACT 2913',
            'descriptive_full_address': '10 Chuter Street, Casey ACT 2913',
            'locality_name': 'CASEY',
            'postcode': '2913',
            'state': 'ACT',
            'street_address': {
                'house_number': '10',
                'street_name': 'CHUTER',
                'street_type': 'ST',
            },
        },
        'SmallBedrooms': 1,
        'MediumBedrooms': 3,
        'LargeBedrooms': 1,
        'Bathrooms': 3,
        'ParkingSpacesCarport': 0,
        'ParkingSpacesGarage': 1,
        'PropertyType': 'FREESTANDING_HOME',
        'YearConstructed': '2012',
        'RoofMaterial': 'CONCRETE_TILE',
        'WallMaterial': 'BRICK_VENEER',
        'LandSlope': 'FLAT_GENTLE',
        'Storeys': '2',
        'BuiltInHeatingOrCooling': False,
        'InGroundPool': False,
        'TennisCourt': False,
        'SolarPanels': False,
        'QualityOfFixtures': 'STANDARD',
        'Verandahs': 1,
        'BalconiesAndDecks': 1,
        'SmallSheds': 0,
        'LargeSheds': 0,
        'FloodingRisk': False,
        'JettyWharfLandingPier': False,
        'BodyCorporateOrStrata': False,
        'SumInsuredHomeCustomAmount': 966343,
        'BuildingExcess': '1100',
        'AssetPublicId': 'pc:47890845',
        'BuildingPublicId': 'pc:51726023',
        'Parties': [
            {
                'IsCustomer': False,
                'PartyGender': 'FEMALE',
                'PartyDOB': '1982-11-05',
                'PartyType': 'Individual',
                'BackendPartyRole': 'PRIMARY_POLICY_HOLDER',
                'PartyId': 'ANON000000049139880',
                'HowManyPriorClaims': '0',
            },
        ],
        'LivingSpaces': 1,
        'DiningRooms': 1,
        'ElectricalWiringReplaced': False,
        'PropertyOccupancyType': 'OWN_AND_LIVE_IN_HOME',
        'ChildOccupants': 0,
        'UsageArrangement': 'OWN_AND_LIVE_IN_HOME',
        'PriorClaims': [],
        'ClaimsMade': False,
    },
}

response = requests.put(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/2.x.x/home/quote/{quote_id}/price',
    cookies=cookies,
    headers=headers,
    json=json_data,
)


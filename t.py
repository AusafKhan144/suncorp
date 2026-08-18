import requests
import json

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'no-cache',
    'content-type': 'application/json',
    'newrelic': 'eyJ2IjpbMCwxXSwiZCI6eyJ0eSI6IkJyb3dzZXIiLCJhYyI6IjM0OTc3NjAiLCJhcCI6IjExMzQyNjc3MTgiLCJpZCI6IjNjYzg4NWViYzNlNDk5ZWYiLCJ0ciI6IjAwOTQ3M2Y4MmMyYzJlNWRiZmEwNTU4MzRmYzZlYTFiIiwidGkiOjE3ODIyMjU5NTM2NDUsInRrIjoiMTUyODQ2In19',
    'origin': 'https://insurance.nrma.com.au',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://insurance.nrma.com.au/get-cover/motor/3/welcome',
    'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-origin',
    'traceparent': '00-009473f82c2c2e5dbfa055834fc6ea1b-3cc885ebc3e499ef-01',
    'tracestate': '152846@nr=0-1-3497760-1134267718-3cc885ebc3e499ef----1782225953645',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    'x-b3-traceid': '086a1ea2ec3a6469',
    'x-iag-consumer-system': 'RETAIL_WEB',
    'x-iag-sales-channel': 'RETAIL_WEB',
    'x-iag-session-id': '19e658e5-2f62-4461-85d3-94cbd1534b24',
    # 'cookie': 'TLTDID=55917568458300382037651968016568; AMCV_0A47356C53E9D4D70A490D44%40AdobeOrg=MCMID|39429057464470909431408251096952311612; _ga=GA1.1.410008125.1772530655; kampyle_userid=87f3-afdd-0651-09a8-7d51-8abe-e97f-09d4; managex={"sessionId":"eb6f7395-86e1-4cdf-b407-f954d2f3343c"}; TLTSID=51050419748011508567261839846928; FEATURE_FLAG_PARTIAL_QUOTE_ENABLED_BRANDS=nrma%2Cstate%2Cami; FEATURE_FLAG_NOVUS_ENABLED_BRANDS=nrma; NOVUS_MFE_ENABLED_PATHS_NRMA=%5B%22identify-car%22%2C%22vin-search%22%2C%22owner-driver-dashboard%22%2C%22optional-benefits%22%5D; _gcl_au=1.1.1549062098.1780315647; kampylePageLoadedTimestamp=1781625640678; DECLINED_DATE=1781626270121; kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_identity=CiYzOTQyOTA1NzQ2NDQ3MDkwOTQzMTQwODI1MTA5Njk1MjMxMTYxMlITCPK8vZjLMxABGAEqBElSTDEwAPABl9LPpe8z; ak_bmsc=4567AB7848CA15AABF92553BA00A2407~000000000000000000000000000000~YAAQE9YsF4b1fLqeAQAAqfOz9ADpgKU4wBp2R8iVfsGDUZqF0bY3CgHbIhWa8ocUFBF4DpXGlg6BdNGVdwuoTb5j5YA/taVjZxUC3HuUKIw9d1it5sl1u7HLxM/VAG1Z8oTz+YcMRt8VKS2Axd2+mP2ZE4hNYPKYupYg06e432peefE5f2tQ8bBHU4CFQboNyWQ4/UcaJ8fRW4mb479L0aEEHp8RrZsxzWfF4l7qqTqkUcYMWr9QkxTkRkIYwPpbBKPAVPttVHAW1Uhsae8ARyD84v96HjbkQ9A3mqg2MOeHjtEPhwRTiIG/ofup209PYW0gFk85DRlFkRBM/YacfQivU6dS0QXLepvT3wQJzk8wvojr1aoi8+V4BNIJQ8Gjl3ZeTEdvWvH15W3hDdY=; _uetsid=dcf32bc06f0811f1b544155b8740ab4b; _uetvid=9c70e0b016e411f19e1f47f58324a57b; x-iag-customer=eyJraWQiOiJYNVdZZFhvcnh4eHBKMzJWUVFvc1FNZDVzTHBqX0ZLN3ZYSnZWUjNiV1lRIiwiYWxnIjoiUlMyNTYifQ.eyJ2ZXIiOjEsImp0aSI6IkFULmY2dFV6SkZoa3lVRnJubWJaTEJDX1E0VThZN1pGRUpUbHN1S1Y0TndMcW8iLCJpc3MiOiJodHRwczovL2lhZy1ucm1hLXNpbHZlci1wcmQub2t0YS5jb20vb2F1dGgyL2F1czdtdzR1aHFoWGhXT0dNM2w2IiwiYXVkIjoiaHR0cHM6Ly93d3cubnJtYS5jb20uYXUiLCJzdWIiOiIwb2E3cXMyYzFjaUNkNmh1cDNsNiIsImlhdCI6MTc4MjIyMjQ3NiwiZXhwIjoxNzgyMjI2MDc2LCJjaWQiOiIwb2E3cXMyYzFjaUNkNmh1cDNsNiIsInNjcCI6WyJwYXltZW50czoqIiwicXVvdGVzOioiXSwiaWRwIjoiT0tUQSIsImlhZy5icmFuZCI6Ik5STUEiLCJpYWcucXVvdGVzIjpbeyJyZWwiOiJPV05FUiIsImlkcyI6WyJRVEVOMDAyMTMyNjc2MiIsIlFURU4wMDIxMzI2ODI2Il19XX0.aQ_6YjMmGtUvlTlHayAePd9bBzDdHaO34y-HG1BPYEGqB3mf-1JwV5WKL6LHSI1mLmf7vchC1p2cxsZ5Va6Tus7WO8-zeQ2BfwabX73RF9jfjxDoQgkymnz4qFfEl8efKx07W4WiiMFGWJ-R5vCmJei-cvTh91a09PYleBa7GMRkVgDVn53nWFGn16MbC0nXvggXapFd1k2ruFl5ir4Vg6Nco6NPueFXxw8JFDS6ijLhkaTUfQYX8KMRbgXY162OLCgHO6-QlnkSGfx1RHG-rddoH_dJzAj-QZbyLZgMW4V10du09MEBMWDHEu92Mxe0ue3nMrSlzuFuVhAZpYVPUg; kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_cluster=irl1; IAG_QB_SELECTED_STATE=NSW; _abck=D7835C04069A487D2C0C7F91B90431EB~0~YAAQsxQgF3J699aeAQAANJDx9BDXNcgVvCntOQnP5CoZeXEIJYXU2s9uDO3eWQ8fPvzpK6xzXbsB04nnBd2x4RGdIiMyIFgY5jLnPHRFOhq11ncoPtLd4lMdlTZaG2Jl9TZQ+1KELrRclJc/XSCF+a5SVcVnG5FoYQIVdyU5eovNPoOZiiU1fMIycsA3oxqtnedhgLpDeGq+Fcc2GQe7dcM5AkqKo9Nmgljg15Qh10ezyjkslebSTyDDv8Mo5NS5kyLpfm1nD6PY5rZmLEvE7Z3wo9sXA7Nl5MBrlYZE5Uv9LVBCO+XdPoDkQbVlymp5HJDQX4BF8EGDmkXB4U7NKOc+gsVUCNm2azLsh4C8xXlcD3OOPE0tw2UnJ4LWIN/+El9o3sowAGtr77BnVHqsEl6hGQezT6JsqZ+d8Gl02sqJeh3lIscukVyaaa0o2gAa1e3SIdvXoZPlheaJDycUGi5j+ZvNhjBGWmO1QHFg7zRw9GKCrsD0xQbHSTarbVJ71l+ZdRX0kHge2gFRJ0r1qexrwVJWd6K01ze18A/+3ePmoSE+tOdZAPjnmYh6ToueV9a8/7z5ejeJtrcLi4k4/1GK139jnJGJUG54QQEpKXRz0eX+3jCZQsuVe0jV9rSIjhaNCPltTfN+JauYQ7N30lx4ItJsUH01A80i8iYmBeVlveQ1E2NHpcWBKAaKyGaPSw+SMAdbwYU6Y6vAY3ynk+umrBAsRd4JP29qiWR9+jUP+JOyf07WmxKJ07mSFczAxSu1gfPXc7FDKWkMzOcqkJAsCf4aU/aBTKgWdZaphLVjk+2jpsq4eiQvzFxmmw51jl3dho1sVkL+R2Hhsh3iivAa+Z3CfTaKfdAw2Fpj+qbkbzOD1FQYnb+6d/mQntV1m0aXzHP+cL+RIRe3jGYwKZ6z8A8ZLb7z8rFlNYNMznIrUlFQUfNkObV+PZb86J8uJd/NavsaTDxBZF5An3NV8ne/vZH74PWWosnl4q1XeJ+XvZjdV2s99kSCS6o=~-1~-1~-1~AAQAAAAF%2f%2f%2f%2f%2fy1oKc%2fwf+gjHC3L7qXuks4ulOIK6RwvZHG+1OgefSc4e2hdivlcmyLd9rbOkGUbsMta3BHt5bEyQYuLfUd5XpAAbZa+jHPAOBANGavB%2fuXv%2f7+m4dZ4UEkXpnEFXbheK%2fJvuNY%3d~-1; bm_sz=002F0F5FA3504C9B1A4B160A14A09438~YAAQsxQgF3N699aeAQAANZDx9AB3aR808Auk0TFS5q5YtiBPPvrYJWIIP5KMfJiAD/5mWTf5pFuj1q4id0lftnKYeQOZXviNNejvBfBPZk8iUvJ0Ox9LesVdaF65Z8bR/ESZ9mP5c8XK48Pkgdux9cNsZZDNoKGUl2W0gZ0vvyAzZfX6MVsvopa8fx8GahuXGYECv/G56+kR6jY8SJRUgrtV91UebV4A68gS2Mjm1LKwNUmFeohWTjmQrot/yfPEWAcAPHvavqWkX83baiTTzwjWkjMc7R/JsRMcag/FsJlIZhziYQFND6gYqTOru+BYI5708HcHsuMjBlKbAPfgUaRvQ4UK6lQYI3OcK6hTzBKcWplA8Zukt0pPFvKFc3virMMSfe+bF8WQFQt53OS6WEi3k5WU65FiJ0rz1oe+4biU/vnyi3WL9/jb91TAH1ZQg1yDDeSeNS6WRQRPu0PR~4339509~3420996; kampyleUserSession=1782225943289; kampyleUserSessionsCount=112; kampyleUserPercentile=56.47265295427404; _ga_FDKY250V0D=GS2.1.s1782225925$o96$g1$t1782225946$j39$l0$h0; kampyleSessionPageCounter=2; rt_slice=6',
}

json_data = {
    'answers': {},
}

response = requests.post(
    'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/experience3/createQuote',
    headers=headers,
    json=json_data,
)

quote_id = response.json().get("quoteId")
quote_id_signature = response.json().get("novusContext").get("quoteIdSignature")
quote_id_signature_expiry = response.json().get("novusContext").get("quoteIdSignatureExpiry")

headers = {
    'accept': 'application/json, text/plain, */*',
    'accept-language': 'en-US,en;q=0.9',
    'cache-control': 'no-cache',
    'content-type': 'application/json',
    'origin': 'https://insurance.nrma.com.au',
    'pragma': 'no-cache',
    'priority': 'u=1, i',
    'referer': 'https://insurance.nrma.com.au/',
    'sec-ch-ua': '"Not)A;Brand";v="8", "Chromium";v="138", "Google Chrome";v="138"',
    'sec-ch-ua-mobile': '?0',
    'sec-ch-ua-platform': '"Linux"',
    'sec-fetch-dest': 'empty',
    'sec-fetch-mode': 'cors',
    'sec-fetch-site': 'same-site',
    'user-agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
    'x-b3-traceid': 'ce1b1c270c212df1',
    # 'cookie': 'TLTDID=55917568458300382037651968016568; AMCV_0A47356C53E9D4D70A490D44%40AdobeOrg=MCMID|39429057464470909431408251096952311612; _ga=GA1.1.410008125.1772530655; managex={"sessionId":"eb6f7395-86e1-4cdf-b407-f954d2f3343c"}; TLTSID=51050419748011508567261839846928; x-browser-session-id=7000eb60-eca2-4db9-8adb-3401e7c24670; _gcl_au=1.1.1549062098.1780315647; kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_identity=CiYzOTQyOTA1NzQ2NDQ3MDkwOTQzMTQwODI1MTA5Njk1MjMxMTYxMlITCPK8vZjLMxABGAEqBElSTDEwAPABl9LPpe8z; ak_bmsc=4567AB7848CA15AABF92553BA00A2407~000000000000000000000000000000~YAAQE9YsF4b1fLqeAQAAqfOz9ADpgKU4wBp2R8iVfsGDUZqF0bY3CgHbIhWa8ocUFBF4DpXGlg6BdNGVdwuoTb5j5YA/taVjZxUC3HuUKIw9d1it5sl1u7HLxM/VAG1Z8oTz+YcMRt8VKS2Axd2+mP2ZE4hNYPKYupYg06e432peefE5f2tQ8bBHU4CFQboNyWQ4/UcaJ8fRW4mb479L0aEEHp8RrZsxzWfF4l7qqTqkUcYMWr9QkxTkRkIYwPpbBKPAVPttVHAW1Uhsae8ARyD84v96HjbkQ9A3mqg2MOeHjtEPhwRTiIG/ofup209PYW0gFk85DRlFkRBM/YacfQivU6dS0QXLepvT3wQJzk8wvojr1aoi8+V4BNIJQ8Gjl3ZeTEdvWvH15W3hDdY=; _uetsid=dcf32bc06f0811f1b544155b8740ab4b; _uetvid=9c70e0b016e411f19e1f47f58324a57b; x-iag-customer=eyJraWQiOiJYNVdZZFhvcnh4eHBKMzJWUVFvc1FNZDVzTHBqX0ZLN3ZYSnZWUjNiV1lRIiwiYWxnIjoiUlMyNTYifQ.eyJ2ZXIiOjEsImp0aSI6IkFULmY2dFV6SkZoa3lVRnJubWJaTEJDX1E0VThZN1pGRUpUbHN1S1Y0TndMcW8iLCJpc3MiOiJodHRwczovL2lhZy1ucm1hLXNpbHZlci1wcmQub2t0YS5jb20vb2F1dGgyL2F1czdtdzR1aHFoWGhXT0dNM2w2IiwiYXVkIjoiaHR0cHM6Ly93d3cubnJtYS5jb20uYXUiLCJzdWIiOiIwb2E3cXMyYzFjaUNkNmh1cDNsNiIsImlhdCI6MTc4MjIyMjQ3NiwiZXhwIjoxNzgyMjI2MDc2LCJjaWQiOiIwb2E3cXMyYzFjaUNkNmh1cDNsNiIsInNjcCI6WyJwYXltZW50czoqIiwicXVvdGVzOioiXSwiaWRwIjoiT0tUQSIsImlhZy5icmFuZCI6Ik5STUEiLCJpYWcucXVvdGVzIjpbeyJyZWwiOiJPV05FUiIsImlkcyI6WyJRVEVOMDAyMTMyNjc2MiIsIlFURU4wMDIxMzI2ODI2Il19XX0.aQ_6YjMmGtUvlTlHayAePd9bBzDdHaO34y-HG1BPYEGqB3mf-1JwV5WKL6LHSI1mLmf7vchC1p2cxsZ5Va6Tus7WO8-zeQ2BfwabX73RF9jfjxDoQgkymnz4qFfEl8efKx07W4WiiMFGWJ-R5vCmJei-cvTh91a09PYleBa7GMRkVgDVn53nWFGn16MbC0nXvggXapFd1k2ruFl5ir4Vg6Nco6NPueFXxw8JFDS6ijLhkaTUfQYX8KMRbgXY162OLCgHO6-QlnkSGfx1RHG-rddoH_dJzAj-QZbyLZgMW4V10du09MEBMWDHEu92Mxe0ue3nMrSlzuFuVhAZpYVPUg; kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_cluster=irl1; IAG_QB_SELECTED_STATE=NSW; _abck=D7835C04069A487D2C0C7F91B90431EB~0~YAAQsxQgF3J699aeAQAANJDx9BDXNcgVvCntOQnP5CoZeXEIJYXU2s9uDO3eWQ8fPvzpK6xzXbsB04nnBd2x4RGdIiMyIFgY5jLnPHRFOhq11ncoPtLd4lMdlTZaG2Jl9TZQ+1KELrRclJc/XSCF+a5SVcVnG5FoYQIVdyU5eovNPoOZiiU1fMIycsA3oxqtnedhgLpDeGq+Fcc2GQe7dcM5AkqKo9Nmgljg15Qh10ezyjkslebSTyDDv8Mo5NS5kyLpfm1nD6PY5rZmLEvE7Z3wo9sXA7Nl5MBrlYZE5Uv9LVBCO+XdPoDkQbVlymp5HJDQX4BF8EGDmkXB4U7NKOc+gsVUCNm2azLsh4C8xXlcD3OOPE0tw2UnJ4LWIN/+El9o3sowAGtr77BnVHqsEl6hGQezT6JsqZ+d8Gl02sqJeh3lIscukVyaaa0o2gAa1e3SIdvXoZPlheaJDycUGi5j+ZvNhjBGWmO1QHFg7zRw9GKCrsD0xQbHSTarbVJ71l+ZdRX0kHge2gFRJ0r1qexrwVJWd6K01ze18A/+3ePmoSE+tOdZAPjnmYh6ToueV9a8/7z5ejeJtrcLi4k4/1GK139jnJGJUG54QQEpKXRz0eX+3jCZQsuVe0jV9rSIjhaNCPltTfN+JauYQ7N30lx4ItJsUH01A80i8iYmBeVlveQ1E2NHpcWBKAaKyGaPSw+SMAdbwYU6Y6vAY3ynk+umrBAsRd4JP29qiWR9+jUP+JOyf07WmxKJ07mSFczAxSu1gfPXc7FDKWkMzOcqkJAsCf4aU/aBTKgWdZaphLVjk+2jpsq4eiQvzFxmmw51jl3dho1sVkL+R2Hhsh3iivAa+Z3CfTaKfdAw2Fpj+qbkbzOD1FQYnb+6d/mQntV1m0aXzHP+cL+RIRe3jGYwKZ6z8A8ZLb7z8rFlNYNMznIrUlFQUfNkObV+PZb86J8uJd/NavsaTDxBZF5An3NV8ne/vZH74PWWosnl4q1XeJ+XvZjdV2s99kSCS6o=~-1~-1~-1~AAQAAAAF%2f%2f%2f%2f%2fy1oKc%2fwf+gjHC3L7qXuks4ulOIK6RwvZHG+1OgefSc4e2hdivlcmyLd9rbOkGUbsMta3BHt5bEyQYuLfUd5XpAAbZa+jHPAOBANGavB%2fuXv%2f7+m4dZ4UEkXpnEFXbheK%2fJvuNY%3d~-1; bm_sz=002F0F5FA3504C9B1A4B160A14A09438~YAAQsxQgF3N699aeAQAANZDx9AB3aR808Auk0TFS5q5YtiBPPvrYJWIIP5KMfJiAD/5mWTf5pFuj1q4id0lftnKYeQOZXviNNejvBfBPZk8iUvJ0Ox9LesVdaF65Z8bR/ESZ9mP5c8XK48Pkgdux9cNsZZDNoKGUl2W0gZ0vvyAzZfX6MVsvopa8fx8GahuXGYECv/G56+kR6jY8SJRUgrtV91UebV4A68gS2Mjm1LKwNUmFeohWTjmQrot/yfPEWAcAPHvavqWkX83baiTTzwjWkjMc7R/JsRMcag/FsJlIZhziYQFND6gYqTOru+BYI5708HcHsuMjBlKbAPfgUaRvQ4UK6lQYI3OcK6hTzBKcWplA8Zukt0pPFvKFc3virMMSfe+bF8WQFQt53OS6WEi3k5WU65FiJ0rz1oe+4biU/vnyi3WL9/jb91TAH1ZQg1yDDeSeNS6WRQRPu0PR~4339509~3420996; _ga_FDKY250V0D=GS2.1.s1782225925$o96$g1$t1782225946$j39$l0$h0; rt_slice=6',
}

json_data = {
    'payload': {
        'quoteId': quote_id,
        'expiry': quote_id_signature_expiry,
    },
    'signature': quote_id_signature,
}

response = requests.post('https://connect.nrma.com.au/cb/anonymous/quote/nrma', cookies=cookies, headers=headers, json=json_data)

access_token = response.json().get("accessToken")
cookies = {"x-iag-customer": access_token}

json_data = {
    'answers': {
        'YearSearch': '2024',
        'MakeSearch': 'BMW',
        'ModelSearch': '3',
        'SelectBodyType': '',
        'SelectTransmissionType': '',
        'SelectVariant': '',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/ProcessingVehicleSearchAlt',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

vehicle = response.json().get("novusContext").get("vehicles")[0]
vehicle_info = vehicle.get("vehicle")
year = vehicle_info.get("year")
make = vehicle_info.get("make")
model = vehicle_info.get("model")
body_type = vehicle_info.get("body_type")
equipment_level = vehicle_info.get("equipment_level")
series = vehicle_info.get("series")
capacity_value_litres = vehicle_info.get("engine").get("capacity_value_litres")
transmission_type = vehicle_info.get("engine").get("transmission_type")


json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
        'Vehicle': vehicle_info,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
        'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

address = {
    "geoLocation": {
        "gnafPID": "GATAS716620828",
        "externalGeoId": "AGATAS716620828",
        "confidence": 2,
        "reliability": 2,
        "levelType": 7,
        "latitude": -43.1652961,
        "longitude": 146.92024802,
    },
    "dpid": "99407040",
    "country": "AUS",
    "display_address_line1": "38 JOHN ST",
    "display_address_line2": "GEEVESTON TAS 7116",
    "full_address": "38 JOHN ST, GEEVESTON TAS 7116",
    "descriptive_full_address": "38 John Street, Geeveston TAS 7116",
    "locality_name": "GEEVESTON",
    "postcode": "7116",
    "state": "TAS",
    "street_address": {
        "house_number": "38",
        "street_name": "JOHN",
        "street_type": "ST",
    },
}


response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
        'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)


json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
		'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'WhereIsYourCarParked': 'GARAGE',
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)


json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
		'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'WhereIsYourCarParked': 'GARAGE',
        'KilometresDriven': 'LESS_THAN_5K',
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)


json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
		'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'WhereIsYourCarParked': 'GARAGE',
        'KilometresDriven': 'LESS_THAN_5K',
        'WhatYouUseYourCarFor': [
            'PRIVATE_USE',
        ],
        'IsThereFinance': False,
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
		'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'WhereIsYourCarParked': 'GARAGE',
        'KilometresDriven': 'LESS_THAN_5K',
        'WhatYouUseYourCarFor': [
            'PRIVATE_USE',
        ],
        'IsThereFinance': False,
        'HasCurrentInsurance': False,
        'SelectCoverType': 'COMPREHENSIVE',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdateProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)

json_data = {
    'answers': {
        'SelectAdditionalCoverages': [],
        'BillingFrequency': 'MONTHLY',
        'AdditionalCoverages.glass_excess_waiver': [],
        'AdditionalCoverages.hire_vehicle_after_incident': [],
        'AdditionalCoverages.choice_of_repairer': [],
        'SumInsured': 'MARKET_VALUE',
        'HowDoYouWantToReceive': 'email_address',
		'Vehicle': vehicle,
        'VariantSearch': json.dumps({"year":year,"make":make,"model":model,"bodyType":body_type,"engineCapacity":capacity_value_litres,"transmission":transmission_type,"equipmentLevel":equipment_level,"series":series}),
        'MakeSearch': make,
        'ModelSearch': model,
        'YearSearch': year,
        'VehicleSearchVIN': 'MakeAndModel',
        'TermsConditionAgreement': True,
        'CoverStartDate': '2026-07-01',
        'AddressLookup': address,
        'WhereIsYourCarParked': 'GARAGE',
        'KilometresDriven': 'LESS_THAN_5K',
        'WhatYouUseYourCarFor': [
            'PRIVATE_USE',
        ],
        'IsThereFinance': False,
        'HasCurrentInsurance': False,
        'SelectCoverType': 'COMPREHENSIVE',
        'BaseExcess': '700',
        'Parties.0.IsOwnerDriver': [
            'Owner',
            'Driver',
        ],
        'Parties.0.IsOwner': True,
        'Parties.0.PartyType': 'Individual',
        'Parties.0.IsCustomer': False,
        'Parties.0.PartyGender': 'MALE',
        'Parties.0.PartyDOB': '1987-09-05',
        'Parties.0.IsDriver': True,
        'Parties.0.AreYouAnExistingCustomer': 'false',
        'VehicleDamages': '__REMOVED__',
        'WhichCarDamages.0': '__REMOVED__',
        'WhichCarDamages.1': '__REMOVED__',
        'WhichCarDamages.2': '__REMOVED__',
    },
}

cookies = {
    'TLTDID': '55917568458300382037651968016568',
    'AMCV_0A47356C53E9D4D70A490D44%40AdobeOrg': 'MCMID|39429057464470909431408251096952311612',
    '_ga': 'GA1.1.410008125.1772530655',
    'kampyle_userid': '87f3-afdd-0651-09a8-7d51-8abe-e97f-09d4',
    'managex': '{"sessionId":"eb6f7395-86e1-4cdf-b407-f954d2f3343c"}',
    'TLTSID': '51050419748011508567261839846928',
    'FEATURE_FLAG_PARTIAL_QUOTE_ENABLED_BRANDS': 'nrma%2Cstate%2Cami',
    'FEATURE_FLAG_NOVUS_ENABLED_BRANDS': 'nrma',
    'NOVUS_MFE_ENABLED_PATHS_NRMA': '%5B%22identify-car%22%2C%22vin-search%22%2C%22owner-driver-dashboard%22%2C%22optional-benefits%22%5D',
    '_gcl_au': '1.1.1549062098.1780315647',
    'kampylePageLoadedTimestamp': '1781625640678',
    'DECLINED_DATE': '1781626270121',
    'kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_identity': 'CiYzOTQyOTA1NzQ2NDQ3MDkwOTQzMTQwODI1MTA5Njk1MjMxMTYxMlITCPK8vZjLMxABGAEqBElSTDEwAPABl9LPpe8z',
    '_uetsid': 'dcf32bc06f0811f1b544155b8740ab4b',
    '_uetvid': '9c70e0b016e411f19e1f47f58324a57b',
    'kndctr_0A47356C53E9D4D70A490D44_AdobeOrg_cluster': 'irl1',
    'ak_bmsc': 'B1C4FA5D3EF095613BC53AA92C75A470~000000000000000000000000000000~YAAQJ54QAr1Pw9WeAQAABIoi9QABGUMjsHtJMYGiUa65rd0xJvNTbGE+GvGBmGBiGdnP38KqaEtvTXx9zC295voUJY2zL7uJ3LZI2ricJGlKF8V0n9uUiXcsiPhOpmGY/Daz+ywjMWKzZ6TUf1NbGN13SV4aqWNOzN9Gj5bSQSiMW4cwH4Q44zTWhPy51XDf/dJKCL9uvDHlQPlGKzZ5I28uOP9bIye9xf6vR3SrH6ToeMz9G9LUx7LD6VTC8OkZnr5fuLT4o6qMR3jZRFwQeGvMx/FhTyQcJHhjIbVnNmhIMrpCmILXKTw/QevBoNwTTE2QtjD/4GAWr3SspI/b9IeDjoBf7Ge435xReZ3ANDjpeVixvfU0F4cCGF3oz7ZkqLpFVAkgz70vq4ffiMw=',
    '_abck': 'D7835C04069A487D2C0C7F91B90431EB~0~YAAQVfV0aO7NqNmeAQAA7pEi9RBZsjp00nEKPrEYQq2Hv/i4HYUsk+CkJ/scJtQT6n2A+f2Fi0fWlXZziYhRRgp71ZUSyeWxlYhspN6YyKbYKdeeoby8EQePq+q9TMqBrdPZPZbwn55cknKJWDf3qmwr1MIm2HAsHf29Tbzy8KukF17xuCD7nscCQzWjntpuzTpPD/PbcadGU+PM3JmXzorMKBOEqOwZE0ZwlP4HFvfYf8ZvkXV+FrXuIS0uNmqvohnaQAFDsDJx2yGfQ6/8tbNg/Fbr1Jn/lPRf/w2rAJ9pBlVrgZBJpxht2YnvI33LUAo65X94yqWkFNek/0/7bdTbXNP+iLFIH/4dNHqCGYtwYTVC8Us29CUkfRQUJNhFb5k5iFV8LWzBNKXvfc6SBFiMXoLRvSLFj+nFDjfE31boQQRxKUnPUZtXhbQWCMce8kxmTv+7BPgXlu4Gx3QPcUXTlV3CMiHHvjawu5dCsJXRm8IcMtCYfbxgW0q3GxaY+A52VUVErObdO7mJO1DDC/09imyJTZGnmYUuyTKg+t/PRIa3QyVbDKPxPhOuQtkWy4OZRr3eFROt302S7QciUtVKMGJa4XROzQ7bSzwHxwHcNK7olTI+TVszCxpa30UfhTGd3mG/rpkarCOcMGuzXcYFZ2EpTBrlAA92hTshyuYWCeVDYnz02q9QvHiNzotTA9UDkb/YXYGnMqGZPkpZAqn9alT6VITb1e61L/xruIqhUqlewjgKoKzDuWCt9RfPjBiUVjU8wr5eeb9oWI0SiHKTcwxay5evYIeexPqsa+UEUzxw5/vQuG9S4PQUc1Viot+KG/3kd9Atf9MzO/+NY2ncZhMSfxxIAASOTZ7v6o9EATs7ai27sMW0+mKCNJb2Fb+Yb/C8wkVO+HjGoqEYW6Fu7CEhzoxY7tWXx70EUMLTfqPJSEiwqqKCQmdI3sVmQQzPPplal0fRhN1UtifYF2ailfwrHuH8n+IfCMNM9hVCS8rujNj7DiFxoz//goZ6mlv84P+ib4qyg2eQ0vyLUXBdKHP4ibyAi1wmAXzboT70FtClmuzGwQESTY1jEANu~-1~-1~-1~AAQAAAAF%2f%2f%2f%2f%2f6XwjEzSOHCFOa9oOe+4jKsJy75Y2mtTBzYmJbxUTEHmUB8QtKocf4GdT7lRC67x9aITr+l11J%2fbg7VnxIX+Oy03meZTvjKv5i+1OKrroB73mAo+cU2O0D8ZflU2JW4u1YY1gf6jImTBy11spBQRXWtHYmqebNLY34QHP%2ff3lQrGmqdPBTkp3VXHBvBMrm3Wr72QZdhMYem7~-1',
    'IAG_QB_SELECTED_STATE': 'NSW',
    'bm_sz': '002F0F5FA3504C9B1A4B160A14A09438~YAAQVfV0aDviqNmeAQAAp+kj9QA8vxytf+kta956ueYpt+jlO+WE3CxXEpAP9Y/Ll934yxLsCysVfN/oDHMUljZQQc+hSwY8lWkhuPnTKoxrQrXrfjGejXwnIGVZnjlBSQHTSDtrWJHqa5fKLXV3NcHaqpsepdtLYP+sorPLGa9NLHv4Rki0z2P9C4CDVJd7L4OcRtRWGje2rYvQMswQgIetaTJ0OVrL3ECZfDUmwuEZc2ldVEEq6LVnHdfrYCll7iCyh/OOOJKLdWVs+h57ZYfE1cxG2C9kk3dQoMdmnfbiQ4VBcD999uLo5SzN9+XjPbKvaApsQUENvV8BCPp+cVBeytz8OYxpaytCLqyIJR8hi+/OAmWj/hDoDXkZVtAhzM8oAnL83lCTxTn5DVtBnDOHjlL8YJt9paECy5NXrTcS2/IjBSPXT4Az9MXpZ+pUW9X9B4XJi9TgAgbs3TxeJwpCMgTmngonfauz+mC49/R8FKM8slil1C9iXA==~4339509~3420996',
    'kampyleUserSession': '1782229235478',
    'kampyleUserSessionsCount': '115',
    'kampyleUserPercentile': '45.70997731269083',
    'x-iag-customer': access_token,
    'x-iag-auth-session': 'eyJraWQiOiJqd3RzaWduZXIiLCJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3ODIyNDAxMDEsImlzcyI6ImJ1c2luZXNzLWFwaS1wcm92aWRlciIsImlhdCI6MTc4MjIyOTMwMSwiYXVkIjoiZ3VpZGV3aXJlLXBjIGd1aWRld2lyZS1iYyIsInN1YiI6ImE3NTcxZmRhYjI2MTk1MDgiLCJpYWcuY29tLmF1XC9zZXNzaW9uLWF1dGhlbnRpY2l0eSI6eyJyZXNvdXJjZXMiOlt7ImlkIjoiUVRFTjAwMjEzMjc0NzciLCJhZGRyZXNzX2lkc19hbmRfaGFzaCI6WyIxMjI0NjkxNjMjOWUyNGZmNzhjMTQ0ZDhlMTZmZjVjZDU5ODU0MmM3Nzk4MGMyYjhkODRiY2Q3YzUyZmY4YzJlNGE2M2U1YjYxNCJdLCJwYXJ0eV9pZHMiOlsiQU5PTjAwMDAwMDA0OTU1MTU3MyJdfV19fQ.SyvhfU0rIHADbpam-baCUP7_-sGLW9frcmmWzihMad2eetzJhXrlAMWYNhkSDZ2xKzGA6P6jIgBdNURQR763VfkkdKhmX1vu5tWfjgxs6qfwlSMcqYR06Da81GWuckBPTNcvOknmu13G6rIw67pI8utbulqAsJAzhqO3rIQmikZLhzOhbqTMa00HO3Rp6OnQfZROJix7nUnP3qRSz__dxzuWhwjrP-0hg4MCpDXuQ5NK8pndSTsVOnGdpOPlx85RUV7FVN7Go4sURHRo59XICUk6EprPMF5EeS0KRYRSTU8XnYvlpkDId8ojuCw6Pdek216hv25Y8Xaj97oOv222-g',
    '_ga_FDKY250V0D': 'GS2.1.s1782229044$o97$g1$t1782229806$j60$l0$h0',
    'kampyleSessionPageCounter': '14',
    'rt_slice': '3',
}

response = requests.post(
    f'https://insurance.nrma.com.au/domain/question/api/question/api/1.x.x/nrma/motor/{quote_id}/targetStep/Experience3UpdatePartiesProcessing',
    cookies=cookies,
    headers=headers,
    json=json_data,
)



response.text
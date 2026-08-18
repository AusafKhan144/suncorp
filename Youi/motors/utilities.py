from datetime import date, datetime
import copy
import json
import os
import random
import time
import pandas as pd
import requests
import asyncio
import re
import logging
from playwright.async_api import async_playwright,TimeoutError as PlaywrightTimeoutError
from requests.exceptions import SSLError

logger = logging.getLogger("youi_runner")
if not logger.handlers:
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler()
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(ch)

MAX_RETRIES = 20
BASE_DELAY = 5  # seconds

def build_cookies():
    import requests

    proxies = {
        "http": "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080",
        "https": "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080",
    }

    url = "https://www.youi.com.au/"

    payload = {}
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:141.0) Gecko/20100101 Firefox/141.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": "https://quote.youi.com.au/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-site",
        "Sec-Fetch-User": "?1",
        "Priority": "u=0, i",
        "TE": "trailers",
        #   'Cookie': 'BROWSER_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJqdGkiOiJiYTU0MTQ1Yy03ZmY1LTRlOTAtOWFmOS01MjAzMWRiNTJhMzYiLCJpYXQiOjE3NTM3ODY4MDl9.XrqMrRQsGTawB4f8a9mA0ohcGWRmB7sOuGYUhAlkoTcNuMiz5AQfPmHGbCBsPXwe3qyjpjMwxiQ3KZRYgi6qYfxafzp1UnGQXLohp7LtB0h0wC6_tJOqIlixrYg0oXORAV9jmecmsOmUcctYzaJI_4RQlmuahC95YvZuGX2Jqx1kuaqMoGEKlIxBPICRigFRDzPaKYkh-04ZXGFwRV56yQrRZG3bk3GwECmgOqm9-3XZZcCNLeW1QEAAtEY7cGbHoSH8OIWQCP5YWPeiMn9PhLQg3yW1FEPeeyPZdVBbB2Gsjb5uqGzhckbPpFTJPC4CpeOxLDW3A447IvrtZ30Lzg; SESSION_TOKEN=eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3NTM4NzMyMDksImlhdCI6MTc1Mzc4NjgwOSwianRpIjoiMzNkMTIxYmEtYThjMC00ZDY4LWJlMTEtMmViNjg1MWMxM2ZkIiwiYXVkIjoiYmE1NDE0NWMtN2ZmNS00ZTkwLTlhZjktNTIwMzFkYjUyYTM2IiwicmYiOiJodHRwczovL3F1b3RlLnlvdWkuY29tLmF1LyJ9.oeuVfMgcwR9aU0SQ2NGzJzaKQTAk-hFgkmQpE0vEzsxBmkVe3kv3za-lPkqbcENqtFNXKc_SZDlE6dB_nzsRCNsxI-gpTE4r3S7PMyeHAnqGsgISeYUYR_22tJaPeSHsXWlJy2Db4bFJrX1EOQtAGuy5kFXtHjT2flYgAwbQrkvpgwJSohQnXxQCL6_M6XuXzv_9GBruGEW1FqkZeegRW-_7mgaCnUiCuWmRTiklGjiMcCda0OqjfmhZNbR_AtPzL8ZGyAhb1XGAdR4HKtmBf9PuebOnTdMrUj7maMUU3OxUYd-D1Hu6w9RZFYXuRroIDwZ5rEvU5agdaJ9I0vSFOg; YOUI_HIT_KEY=33d121ba-a8c0-4d68-be11-2eb6851c13fd'
    }

    response = requests.request(
        "GET",
        url,
        headers=headers,
        data=payload,
        proxies=proxies,
    )

    cookies = {
        'OClmoOot': 'AwAG3ZSZAQAAYHagszO-8xawzPLeEQspRIlL_HhjvLC36g_Lno5gO6AjEUhpAS7KO1KcuGbRwH8AADQwAAAAAA|1|1|001d7d64eb4e64912b1f450da52f1dcf8d5c8219',
        'Lyp1CWKh': 'AwUYJpuZAQAApqzeWf5Fx72niygzxaRjcfJjudTNhIG1nrMrQkwFNrcG8GPwAS7KO6ecuGbRwH8AADQwAAAAAA==',
        'HOaYWcNh': 'A61yBN2ZAQAAsiR0MBVwOKsiFwvDlk8I8-hlq2LTDwct_B5Pzx1B6hdqtCKWAaSZImCcuGbRwH8AADQwAAAAAA==',
        '_ga_T941HWW5GL': 'GS2.1.s1760431241$o7$g1$t1760431591$j28$l0$h1399645649',
        'BROWSER_TOKEN': 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJqdGkiOiIwOWE2MTgwYi01NDliLTQ1ZDItYjg3Ny05NTJhMDExYjk1MTUiLCJpYXQiOjE3NTk5MjAwMDZ9.eSjZS_QuJDy6RpR9laHaPkX1BF5bNzDZ52_dc58PSUOch1iarvzuZ_ytUEV4IfzTQtYXMM2ROpuHxyDMJnP_hgJKP0p_KG5hYKox8-AqGJfj9n24pOIktyQQPmnLxziaQSyjYaCoPsgVUBRwb_UcfJpg6z_9sdHBbXdNxYr2IHpMVihSRiBffcT42X12oGJj9BkYQygCBxX1CaXgs3ILLMqJKx-KkNTfAEnaCu8vWpfo-_-wkIrjhC0sj2oTX4Jrf9nfDJxAPb7E1E1E2ebHs9viBlk5AsU6lQT4YlRWoz-_hiqBCYh4kE29Fv3YJJIJQHM7lPDCrBvn8UlZvmaO1Q',
        '_gcl_au': '1.1.1891573463.1759920007',
        'FPID': 'FPID2.3.a1BwaSCuKpMWFbN6eJNGSyaE5bjmCDWO2HHr4j1bjJw%3D.1759139151',
        '_ga': 'GA1.1.276885132.1759920007',
        'cf_clearance': 'T5lODXX92Vgn4vh2Pur5XCWOG9bicmeUhVyxCNmKog0-1760431235-1.2.1.1-ntwftcKYsdMZlFCmKT2TsbmP4AfUxPh7P04.LStGqsa80.srHxPxVqURfT.DQjF_OGDksVoKgCkaJfJtROJUsqTM8ve2OZqylMoC4diO5RAoy6JKgiLGLi1E8Sxyo2K5wxL8my2AikDctgBFcB1n50zDnJSxdPZYnU3_dpUheI2JBC.e8KWGfrTzUennCIGnhCTMRONdevEX3ndx0Q.IC8OF.g74CVQcXBHo3KuxZU4',
        'insightech_vid': '197c0ff64f5.7f1c8',
        'FPAU': '1.1.1891573463.1759920007',
        'afx_profile_brid_csid': 'b1e6e8ae725942729927fab195b6aa238de06570d837923',
        '_fbp': 'fb.2.1759920011108.662176302539689441',
        'afx_profile_brid': '%7b%22acid%22%3a%22c5bdf5401a56a1332dd73a0dcae7a3d84c382593%22%2c%22brid%22%3a%2289RPUMDEW8QgBjLUl5XtlIOTAwkoFKP90GnzhBLnRC6cdcptLoHhZHMcYetSUJ4sJKliqmsrEnPzQqH6OTjIPTQe7%2byTVFXyM5jgvdRN8%2fyqGDoktVXSqFLu%2bpE8B%2b8pWBqDsaZ50Hz5NPZZFBRqGaCt32TrZVU0KM7SmeUb4EQVGKwbCBDYB%2bg33TWPOhKzvFD4hDssWCn0GRnGaiLknw%3d%3d%22%2c%22ibrid%22%3a%22%22%2c%22md%22%3a%222025-06-30T13%3a20%3a58.724Z%22%2c%22consent%22%3a%7b%22dnt%22%3afalse%7d%2c%22pv%22%3a%222025.6.13.1%22%7d',
        'SESSION_TOKEN': 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3NjA1MTc2MzIsImlhdCI6MTc2MDQzMTIzMiwianRpIjoiYWYzY2YzODItYjdmYS00NDJhLTkxZmItYTI4ZWYxNTA3ZmIxIiwiYXVkIjoiMDlhNjE4MGItNTQ5Yi00NWQyLWI4NzctOTUyYTAxMWI5NTE1In0.lgiTyIPHNTyOvtOhBuQC44gRis2ZlD7Erl649_zbJA4Eum0Ft0yBRwI-ecu_9t1D6ZZ5g-w4AYg-6Q-dpbk1_1U0VuI2dUn2f0BsNyUk8C-Crufy7nO23C7_9JtvHzUFRI9zOPC_vn6A7NvULdW-Zixy21Fpe488arUN0xGsr0nrtzzQB1KrClHdSBZxC51IYL7X7wKuOpUeD9Rh22w-uDJGIfaRDgI6-_BgDm-Fwi90tdFwVqRVTcqGTLuTHTlyvNdz9DaAAjP9W6u2Oq1rzodeV5fneFzx_CbefVcFiBByZUQvD1fcC6UlgmSOb7mdii54JFn_RXbmdYc1DKC2UA',
        'YOUI_HIT_KEY': 'af3cf382-b7fa-442a-91fb-a28ef1507fb1',
        'DC_UID': 'AMsySZY6kOTOJx1L7_11wqhSJVa0',
        'FPLC': 'xIblv%2BbDcA81VbPYRnvnnJ8AOb65IyN5ux6nTR%2B4R9T9JoKE%2FkBRYivpcLtv%2BI30TKqI3HqoNR3%2Bzai1gZ6ga1qgYUO06QUkKmaDTqG80O1DQLF6EtWufrp4BPrvXQ%3D%3D',
        '275d1464-228a-5b8d-79a2-b7e244b6b2ff': 'npnNIYS7yvjpxgZWFyVrP5Ucr%2FY%3D%7CWU9VSV9RVU9URVM%3D',
        '275d1464-228a-5b8d-79a2-b7e244b6b2ff~': 'DXxCpWDcMNMs2718MTqa1w3711qDiXB8E8QLy6wJAgY%3D',
        '885703': 'RF2XTIFOUTMxZyTpyTX6omNLGWlq02lN2tvkIguh+mGGU/rCABn5cXy+OqyRaWPXtTtV+dZ0Ws70jDajaazleCN6GrcspPuYqlFXfN9TFie8e07+QZ4fzEhXzzu14qUI2N+KYudh5ubcaDB2d0TuQYxjLxLN/uTrsaN0ybY6PcDDMhew',
        'TS01dc4fc6': '01a8c3f7db75f316ac55de9c4e6975379a207e6724d806e4d111dbc4d27caae9cad43a712b025b6c42a529770499135a707e356487',
        'afx_profile_brid_da': '1',
        '_uetsid': '8a994c50a81b11f0b5a7fdc61951b6ef',
        '_uetvid': '29cdef70a43311f0a148aff6fe0e4999',
    }

    return {**dict(response.cookies.items()), **cookies}

async def get_headers_playwright(url=None):
    PROXY_SERVER = "http://proxy.goproxies.com:1080"
    PROXY_USERNAME = "customer-watchmycompetitor"
    PROXY_PASSWORD = "DKo3FPU_qpNJ"

    PROXY_SERVER = "http://geo.iproyal.com:12321"
    PROXY_USERNAME = "4bXEu9gUjtuJmTj9"
    PROXY_PASSWORD = "cxSaLf1A3F0FudRz"

    PROXY_SERVER = "http://shp-watchmycompetitor-us-v00001.tp-ns.com:80"
    PROXY_USERNAME = "watchmycompetitor"#"customer-watchmycompetitor"
    PROXY_PASSWORD = "dmt276gnw845"#"DKo3FPU_qpNJ"

    # Fresh flow id might be better than hardcoding, but keeping your sample URL:
    rehyderate = True
    if not url:
        rehyderate = False
        url = "https://quote.youi.com.au/createquote/oavehplf/?leadType=sf&leadSource=&placementKey=&promotionCode=000&promotionNumber=&promotionCodeWord="

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(headless=True)
                context = await browser.new_context(
                    proxy={
                        "server": PROXY_SERVER,
                        "username": PROXY_USERNAME,
                        "password": PROXY_PASSWORD,
                    },
                    user_agent="Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/103.0.0.0 Safari/537.36",
                )
                page = await context.new_page()

                # small random delay before navigation
                await asyncio.sleep(random.uniform(2, 5))
                try:
                    logger.info(
                        f"Navigating to {url} (attempt {attempt}/{MAX_RETRIES})"
                    )
                    response = await page.goto(
                        url, wait_until="domcontentloaded", timeout=60000
                    )
                    # headers = response.request.headers
                except PlaywrightTimeoutError:
                    raise RuntimeError("Page navigation timed out.")
                except Exception as e:
                    raise RuntimeError(f"Navigation error: {e}")

                # confirm page loaded
                await page.wait_for_selector("body", timeout=30000)

                cookies_list: list = await context.cookies()
                cookie_dict: dict = {
                    cookie["name"]: cookie["value"] for cookie in cookies_list
                }

                match = re.search(r"/online/([a-zA-Z0-9-]+)", page.url)
                headers:dict ={
                    'HQSudwpRPP-f': 'A5GUOCScAQAA6U6EfyJ0ac3stBcfIvpCUTscK2KQ6nInXOvv-sQTeuaITNC2AapL_4OcuI1HwH8AADQwAAAAAA==',
                    'HQSudwpRPP-b': 'ii9aj0',
                    'HQSudwpRPP-c': 'AMD0HyScAQAAi_90f0w-CuW-5VJ_QyioJmQOBnGiq_zqr-_f7BYi-v0pIWb6',
                    'HQSudwpRPP-d': 'ADYAhICBAKGAgQGAAYAQgISigaIAwAGAyPpCxg_13ocRnsD_CLzTABYi-v0pIWb6_____4Z3c6cCNx4EfK3ZMU5c9mIDZNrbLg',
                    'HQSudwpRPP-z': 'q',
                    'HQSudwpRPP-a': 'YxO1a-kbrtfqUVMfWnT2mCgPDmuEwCy2exoSnfZB8o7cCtERm=ZVQZsDG6HvuPsjyXDb8eikwH63rhpWBVcToS43ipTHaEmlDhsgaIwrUTNUirdDuUNbuQLtOUt3Px43lI3nWBRxs8uf86nYDPsAxZHdt7EzHePA_fOtSX6rV3MR5jjF1_T8gm8BpOR6zpllOGuHmGnZgsE5exNV74wVO7uIJi4edakqv7GpR=PceYcjPTkqcoy-ZEHSeaLJVfbu3qA36kZ=uDr2Vw7x_eMtY-4p4EY=2hOTSzINg1CUBgwzR-qXnohwYz72JhlhZsCcslP1z81MRylJxA4MSM7JtqLpujnRjLotmddmhWVGQN-mdohu9woCLLHrGnthefVYM1SY1QpH07lp2CSpCsPPkmnVkTBA7m1G9ZYbIzsPn2UUI1MmT=mNaWndweyczFnLICoXSpVC2PrPGq0f7jaM3tV6hUsvlBAa1jDshPF2ssAAWNAxM632TfU6erdoSZwS=cd-amw6Ol5FnltS9Ps0jUG1r8CGd-8l0pgpjTwZmPElNNPi7MB7aqhkItNxycllb_d-Jpy0TDhGjWJJ48Li4=ED=2Cd64wIb1k4k52xXoQiObo7o4MtSlkoDMtFqrCqEUyPB2hBNLYBWu8EZL_xxrHjcdOwyJIidX9pPEYa5j-G_9ktDmHYQwHTVh7W3cBpw64NUmCW8FsO5SyWOzQwMqqLGv=9iewZH3-mPwq79IiRO2Lk8t31I5jY9BtqI=cVsa0Jhe0JCBpYnyzN1wgZm_icEymlpd14177DdwHYEvMJuShqxlyxcUjc4kbl3MIzVNCdx_wmvTyDflNThgk=yHR31Jtg4ZxvQqfpUoniQn28tqIwsjw8ErTcxreD0g5fdBJPQSeZJT6Rgy3mrPphxlvX_nEZ8dqSXeHBeFZz03qeihpxaGwxypcHyI8gx0LQeQqg=8YMlM34MfHbCMVLhljjLoEZMYDzDFWraWmtLPMXMTRcXeN84r9Ibrbdz=dBrUPuQR1n8mAIQ2qB6_5LzIsD_i6LqJ3HP5mQ0QuwpBkeNl92Waujf7HDIIQuhGzv8tpw8I3PlnBqh1Yn8GsaT5sf=9mrQjYFArlw4pgvgN3zaZ4D8TBhzRDS315e2ofcGU7RAHNnPVM02dPd9_HjVHIguL1s4ytmFUIqHIjP8lkCbT1VtluLgWHH4gqJtJ=1JhCFuXGFiWsyhrOL1=VlJb7S=vxrxDoApv9PcIynS3NOjHkBHPyoim1eN4Sruw=M-VaRhR-z7XLE3sMjuPZJvlRpTUDvsEzYgwJ1nkO5q9rcv7PIs2ChTlURCPv7g_DvqlFN_PYXbTc2BJLu_0bXC3SJmYSCbMW8ZQHtqwUX41BtG-ISZgp4nxyBApjUQVztBbZYYx4rYsm1DMa6Ngj7_YX7LonhR3=0mQ-Rn3pm_hI_Y_cUlnt9X6_ww2muSN5baa48ymckaQlNxlR4_z-4DDCBlyw7xI=yklYGohm5c_zOhmhh1ZRiTZpuIuwPqzOA74VssEVzon4Zyxn72tjJmU6bl1anuHEh3rI_pmp7l_DD7oaO7km3TzP0DyBVXqrPrMsEPL7Li1qjPb5eYntPwn1X4hoUYB=syznlVIy1lXtU9MxC2gfVEI5zkTY5oN1C5TZiIfDS2OYFnmH6DrhmEqBtYolh02vReI3dV_4Ml0WUNghPOvLsJNaEZGc0xP3br=IL6-bXB=h1sj8dUydFY9LdHwlLluntDQUhpl6133YIs6k0Xo7oMsv7abFN9xv0wUlXVGU2S9CRG8xMNYc5UJ5yFAf46umBINIwGb6ukrL5q=FfFu-W9SN-BPr65FjTjoce-ZP0trW584oxXW7F7V01y5y=4TY6fWTtG9Up8-BR716p7DTPmPMi1kuP6OSrvZpPDaAuGLeCsYllMVXewCwxE10Qoay2n=R82QLy0yQ4=0_cYYcf2Ie0w0=w1W_muN-sSGcZ9HU9ySu5hwZYL7FjoUQttPf-wFE9=q9RkehnRl34Qvj7Va4ERzRfejjnW3PY0-U2aDNUXB40Dql21wC-x6fkY2kcFP-L-ll9raf-eynL1-m2Afjeg0vu=Az_Rt5xOmgd3I5mx=pPrcSUqM9m4yMcV44HaavXq7ykP=rdWuE4FS6iCmxG-ZwLheCjiGc0l4ImYokr5MWSzjVsemChs0J6fPd1kRPCgbqS3GmaJsZEjNZ8_89oGhIFXEHCB1SexnwrlAQ-MfjzxmTP-PhWzzWzL3Vs0w_g4Mt9BJimLb6ow2uQHhC71qblau=SOMAhYvVEw6c0ETeEl7L8woErp=pe2UD8DSy3uhe7LzM7gXdSHioPe5SZeWrHDb3D9xOkDwslHic4kjtlvmPy49ViM5G-4gsiowylqMLvUzH9o0BVf4Gor1bcUlAboFREXCO74t5EbJIJoBm9DAARdWRiQfj1SnTat_X=NEMLYEmCr_dNtjs96ul7JhM3QkrCZ0IhMeXGWPF_icQqofxJAivBjZYAUACnssec8xqxB=tYMWlvxNx--=7bGHm7sNZldpWXM5P60xMFrR6fLJUomwyJ4HkCHV=y5uTY5aBW7O0P3MI9HsQvJRYaxLxasuZ7RJf_l84x06F_TsLRsOjfrmcm2WhhlLvxul8HfyZQxh-FJJGm==_HIMWNWuhb5ouRtYzMOBH5XeIZqvP5jW=tlwGN1jid0=HFEGz5ngQjtO_IqcGtALAqdSdRbI6BSR-rXdVdCwDtf7h0oLLv_qRX5CVGol14i3cyhYhV=T-FAWr-n-8R2kpjLVlhEkrWnLgTO=eGRHrGLMXi0HbUi4zf0mWT2JDTtT5jakfWBWZ3OEDb=ilCSNgE2Q2af94YY5y3lpMqDyLVethLy4-Ces7Sj2FLgwrI_53ZN4ag7yolUF2PNEUSE-mQabCyi1b2iO4yE0hYinv2Bx75sk9Pc4Lgl4TM=0Q5eQUUglvlF=WhJedc6FOBrv3VgTEd4=wuyXQ-VedaO6MlaUCcDl1V0pngPw8OZc2Gg8QlEb5tnceYOEDTX134LPUFbeD7aLgXS4qOxoVjMGshNJot-MaHwjY2BJEuuRvAvt=1A7lZoZ4dEdP0-biOnTIJ4Vjx62JNqRTnaUPEZbxyiopgiaTFL1gewwQBhn_FNnCU-ryw2T1kDJ9q95DVOAEQz4b0vPolC5CxqhGfcfQhIZr=Z2s2n=VFb15SNGb6biC1Bk63QduDg-F8iug_pgCQo1EecEaECwhBQWBbOcPDUCNgxCmHejUpjDQLiWyu_COEUOO_a0siQr5V8YFwloBwqc41eZnJE0NM0A5LJtpIhdJ=y9Z0nZ4t=eaz_X9VRaRQ5F5GeiEaHAo05FHiJVmNyvOCWEQCcG5NdYTfWZt4CWFhWoQ71-kCJS8TlXjhJsuJDkD5y-89VnEat6_ZG9cIDhhQZ-WPsCBUwW8g5bsx=jxUZhgE4_xHNsUzU6LMc8TCbQVEXyGqua88tF=_80DGP4Xs01Jn-uRtZeWaPBG6pYu-H-d0=n7xbIV=74JrALCCnrNzYXLcWDv-fuWEx88hZ7zxJv9SAAJDO95Y=XU-9wi-=kqGNv2zH_lHz0dBPQ-A-w1IfFliyrJHNJcSHPYkTR=zOnljIQtqJnvtkhXvIoswhIFlGPRHhwmCSQXzz7HH4ltOgwsCoQL7rbRPoiI490lohFbibxpIW9ZvkG967A0MOkHv8wmEmeGttZfmy1mq=_NtrTwtiVaaz9VLo95dpuyht5uAXEPh4d6DMZ5Hpt5Jkyi-qndywnMMLlLhas5G3Dc5k=g1DF3msjgN_uTx3c2ZsY97jhL8D5FEQgGE7tNisEPC2X-pYBuyMpbzgmSAbPe=lS7v2vuMukCpT5tNN_s10GyDOudGzP=IVS1CcH0JiODrzgVRyc=eP9l2w_7hu2B6ORYWlFNkPaqJlL4UNsChGBTe5abhya_ctkSrLnjva6d9VB_bnnFXkd1JvfTZhL6xRGLt4Q928AZ7FlWjn2e5jkMpZ214yH=jT=SYmU7ysgjGgfVz7wefGFs6M-9hFhWouPmYVIuqIYRbYuPXZUJUDlkArYpJ9NC6T8v=-vFDn0UBW59Tjz01EWjGTBsUlRlCJN6=ciIvANkemjc52tup9MLSl3n2ggzkhPAuufwjhFPm_OTXE97=9xaW33q=4r-kofIkc5wa9RxpzcigcX6RQSAfnSvGCzHtUvHoegjDdCBM8tAS32f_zrf=yDWbSvysaIfMsqEOCSB3HIRCa_aJXZwkDIc52ycJQEAhwoX2XW4xGYwvueJ5W8RMDuMF_ELNpmFno7OldURipD9pM-llOzm229X0GIhkJLD1XbuhAPDCja-u9TFAlX4xd0iwMkoWjRgjNHPzVr1WE4qX-PGYN7-wa7y7WQjt3aBOwbSCfWWkCfSjqy_rBLO5ruh00ZNOAlyhjkh6r7qFJhSzcLOTUy_WzfUw=ekHUqnwHh3V320AFcf1-fB=wsJ=U5lpX4-piVd9L=951sCzZXzHM0JQIuP5upC1_c4eX6E-m4T0L9vYwk4jmajehtgA69cUrFk0D-vpy=zWyg7jESAv4Lm6a9D3FuyHeognRCj70TkII4AdBE=m_u3B4Yn_4ScDvfZFLYiWOfMlq64eU1Iv2-t6oIf0eJWRIU7r10sh5YjTun2eaF0LBN5iv1FEvQ=cLuF2eh_T_N-OJ35eP6AhUg1Yi=O4yh5gsuqci2McrkEh7uRLy0e9vRVsnLTBxFviIFYUx3NCLipnO6yrDFA-kp2Ag1yGr6iu1OXCgID4IDLesXliquvvYPgRf-C4zx1WM-0N9G-gcGIAGqASolIkDNM2mgPfsLhA5Bitammpacpz53Pztd5bUnh4ugDg9uUQZyc8EG4QjIBAS3mj3pXBeo74qXOHk2fCqwrX9VeDHgRGM-m8FZ05DOJbigLEANMpLea3TjbYFxqy6wbrElwr8Rih3rZ8e6Vmjm9ijR_nH6ll0En9A94441JrJgDMiXXfL-f0i7ppjil0gxy3T=QgX0DrY10Whi67yXIvLHQulM7LW1y7Xn-f_qsSONsc7D3aL8-AzO=FciMiIY-c=Pg=fT5ag3_1eHCx59Vi8p4kLyI67p1pEwyze-BudfnYYmr4Xfbgr9C=O1vldBEM=m27Cj---mVGvZ3tkmidcI0ibzv8iQBfHHZQk3BcmFVxUfEeRqF21M0n0j45jPYoGv2X7NedOzoDTjmHru79DBSRkLM8O2FNrXbBvVY43vbY_loTs2zAp1uDsP4fB9OqLiJOOOj5NUJuMo1ZN6Bjc6p6f9hdVnr=sAYwGcu=HAUHHTfz_oJfkNAuvbZIeSuUsLcfzWipmh4PeiOWCHJrSFBAqg3DB-Q825czSRqqdlnls2MYofZkZXCPpUWdfQodrDG4t2ZEeRSn2NntkyYl6n-DNEiTRnN=wwMWq4SSBrsTQBhbrUgdFnth5-limnhCE9bkN-M_9qumYxdS-n79mDGs6vxsnWn_2bOC-z-AfNJ4PHbH1CUiXoWU5-rZjpAiBhAcD_pG=lH1__Y471ZyhBG_wWyaYHl3arI-xEDkj4gB5xT891zjVPUCQJWPFUsPlNV0UUMi0ft6SUcA5OUEqJzD=XW4ZDvNk7g3OMQYUR1C=Qr_dCNbV-vMJqIF6fLu4k=cWQevOVuR==4vy5tQT6sjUbLhYT2HFVqIvhpklGdM0jisIYClOyeP0-rfcl=a8j5YBd61=spokyPJX05mHYjXm7D_N=iPTybp5NqW1td08g67gWPUC0uHp2Na7EMvLmJML2iRMtiYj5UA3qVsSvPm9uuiAmrJmiU1b_O9gQdBfoqW=EvVuz5sGdzclbHBuZrlgMYkqb8H9SC2G0wsa7E6TStp8jNE-G=YzuTovyBNtPnp3bOU6pu3BNt7bMd28o3kZ2YDwLoVbFl253Hi_WgZpbw5bimcJNuPlIhNlgOw66V9Ha2IjPi9Suk2X8modrs2lfn3iDp1WJPNJXcVPkJqgMJEXgpQWvZ3Inm_CpC-Lp_=l1EQk-oafSY4vs19PPRrZEsOkTdcpmceg9wl3XOoAit1YGX3sxnTRjUY7L0ngqNTAo4TeL10x7qUGGAmik7wMUUunNqoZ-DbuQThZRkeoDs3aj8rM4QRUWbQsiuabtCJX8W8oiOwpaYXTb0Dgot1aC8vtdJxeeVZQvuUs6Lc4_vFreI067BtlPc-Ib2_DOlN9bscJQvmhqY5yvZHsV2iFJg2HN-sJ-xnOTEj52wglMJUuBqV9s0dA0momIkBTVjBIiY3tnxS0ut229zvCt68hsrOuIuO4P3aSMhJ46g2ZZjMdPco7HXxRQlaIvCnbspZXm0r_oyvEMe1UE-A7LoEstpgvNicDFIiYbEvd8c=obfbf_zvheLnHmA3xiBWvqUYuTNmPng1hGFX3zQi3HwqlmEA06kzZTCwnmSVEomU9ri6ltBJLCIiXTcjO-UGiYoTrW52Av7fpjr=L9LhRoYXQYEacLTLQxw-D6U74qB6UTcB=3CptJsb97pVgUXqIN8s=pweH-BwrDLsMA3Js5xlSx3ArXsilW6V=apV3beDYyxVVMCGlRA3nsVbpLlashkMPqQR6sjI1P=Vw7kOme8JQQPGlOCh0OLkPQmWGVSFYZysU6J4mZNc1uuQr8HIqZ0UcHduDNQS09tVtgNJHr_YtPVetcq3CcqMptkbGOPnQM2UPS9FiJdqvCXtUpwwhA035yloj9DaVf8ylMkPlFIIZQ_Nq24POrUHZJr-9iOqQ1gj6sF8LMmvHLw_XtcowZXW_zy_3jsDTDUm-oOal1BACctTxhgIoJGv5qvrpJNV1a63H681pI2spYe3oiFZk97QbNt1rmEMVFu6mVUre_PwYO1AtQGiDTUFx7gE-hfA8vcM4v2HiMUUYjF-H81wXO=BT48x2c7vSs1hsTfZvPvg0yWMvcIylN7z_Gy40nhH78N3dYPl=Qtig03mAFSZC=zhiY6DWP5nhTUll39DAydShnHiAVxAyqEobYM9XF=f5dXnVeRdmCdF3S_cwQGhvsfkzwQ9WbSt5aHa68ireBRBOvBdCrGx_zTXzOG8h16WOl0brpkNM8_qnQwJgg7M7xcDvaBbH6=qA4LMvGf6fFl=QrvRVzZnFs99vIXWT69Q0hiCUaSdUFkqRhc3kIWzyzmXg1jIRJcGW=FV3TMyRel',
    
    
                }
                flowInstanceId = None
                if rehyderate:
                    uuid_regex = re.compile(
                        r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
                    )

                    uuid_keys = [k for k in cookie_dict.keys() if uuid_regex.match(k)]

                    flowInstanceId = uuid_keys[0]
                    flowInstanceSession = f"rehydratequote-{flowInstanceId}"
                else:
                    if match:
                        flowInstanceId = match.group(1)
                        flowInstanceSession = f"oavehplf-{flowInstanceId}"

                headers.update(
                    {
                        "User-Agent": "PostmanRuntime/7.32.3",
                        "Content-Type": "application/json",
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Connection": "keep-alive",
                        "Youi-Flow-Instance-ID": flowInstanceId,
                        "Youi-Flow-Session-Key": flowInstanceSession,
                        "Origin": "https://quote.youi.com.au",
                        "Referer": f"https://quote.youi.com.au/quote/online/{flowInstanceId}",
                    }
                )
                cookies = build_cookies()
                filtered_cookies = {**{k: v for k, v in cookie_dict.items()}, **cookies}

                # input("WAIT")
                # await browser.close()
                # logger.info("Successfully retrieved headers and cookies.")
                logger.debug(f"Successfully retrieved {filtered_cookies} cookies.")
                logger.debug(f"Successfully retrieved {headers} headers")
                logger.debug(f"Successfully retrieved {flowInstanceId}")
                if not flowInstanceId:
                    raise ValueError("Flow instance id is none")
                return filtered_cookies, headers, flowInstanceId

        except Exception as e:
            logger.info(f"Attempt {attempt} failed: {e}")
            if attempt < MAX_RETRIES:
                backoff = BASE_DELAY
                logger.info(f"Retrying in {backoff:.1f}s...")
                await asyncio.sleep(backoff)
                continue
            else:
                logger.info(f"All {MAX_RETRIES} attempts failed. Giving up.")
                raise

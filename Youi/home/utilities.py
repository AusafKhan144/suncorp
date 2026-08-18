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
import urllib3



urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

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

    YOUR_API_KEY = "4d39f5403fa24cf6a31cf8873b3d9101"
    go_proxy = "https://customer-watchmycompetitor-country-au:DKo3FPU_qpNJ@proxy.goproxies.com:1080"
    oxy_lab_proxy = "http://customer-wmc_user_aakFe:WmcLetmein_123@pr.oxylabs.io:10002"
    zyte_data_api = f"http://{YOUR_API_KEY}:@api.zyte.com:8011/"

    proxies = {"http": zyte_data_api, "https": zyte_data_api}

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
        verify=False,
    )

    cookies = {
        'OClmoOot': 'AwAG3ZSZAQAAYHagszO-8xawzPLeEQspRIlL_HhjvLC36g_Lno5gO6AjEUhpAS7KO1KcuGbRwH8AADQwAAAAAA|1|1|001d7d64eb4e64912b1f450da52f1dcf8d5c8219',
        'Lyp1CWKh': 'AwUYJpuZAQAApqzeWf5Fx72niygzxaRjcfJjudTNhIG1nrMrQkwFNrcG8GPwAS7KO6ecuGbRwH8AADQwAAAAAA==',
        'HOaYWcNh': 'AzMnHQmcAQAARo7o0FXNimLcGr9GxPLp7LVItyXPVjiBZewc1K2t-84mjWgjAQ7J9V-cuI1HwH8AADQwAAAAAA==',
        'BROWSER_TOKEN': 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJqdGkiOiI4MDY2MTU3Mi05MTQwLTRmZTQtOWMyYi03MTIyOTNjMjUwMGMiLCJpYXQiOjE3NjczNDU4NDd9.LvAv2szrCusZ2Fpx8uF0pg5oJR1m5lbIXi6dLXdtHFxBx_cqGlEi56Tw5prJ8YMIq8nL9iblYkBzzmGEKqS3CDN9EnLgGMYTkUpYiA3iIt8Q6_V9fTgpvQDXCVTunh_i_O2LeNChgBYpNiV5yJdNh26858MiGbP5CiEjt7EoSYmXQvFUp09QsCRm7tsFZv3KJmZHiBq_gWKGIsfJ2TFBf2FW5SncwSGPqtO1srcibrIqrzosNWywmgxZ4ys00uYrDCo96kyVFahirMmqCp-tFO0Q6icEjT7XYcGSJNXN_cYCIPZS4tZKtW6x9Iw0VLJjfwyyukfFTJNmkWwvxYMAEw',
        '_ga_T941HWW5GL': 'GS2.1.s1769679418$o44$g1$t1769679434$j44$l0$h208676835',
        '_gcl_au': '1.1.1337157123.1767345847',
        '_ga': 'GA1.1.152216536.1767345847',
        'afx_profile_brid': '%7b%22acid%22%3a%22c5bdf5401a56a1332dd73a0dcae7a3d84c382593%22%2c%22brid%22%3a%2289RPUMDEW8QgBjLUl5XtlIOTAwkoFKP90GnzhBLnRC6cdcptLoHhZHMcYetSUJ4sJKliqmsrEnPzQqH6OTjIPTQe7%2byTVFXyM5jgvdRN8%2fyqGDoktVXSqFLu%2bpE8B%2b8pWBqDsaZ50Hz5NPZZFBRqGaCt32TrZVU0KM7SmeUb4EQVGKwbCBDYB%2bg33TWPOhKzvFD4hDssWCn0GRnGaiLknw%3d%3d%22%2c%22ibrid%22%3a%22%22%2c%22md%22%3a%222025-06-30T13%3a20%3a58.724Z%22%2c%22consent%22%3a%7b%22dnt%22%3afalse%7d%2c%22pv%22%3a%222025.6.13.1%22%7d',
        'insightech_vid': '197c0ff64f5.7f1c8',
        'FPID': 'FPID2.3.0npp2x5IHA8on%2BetaputzIw%2FRim5c1uYpmDwZe4dSr8%3D.1767345847',
        'afx_profile_brid_csid': 'dc710d9865844cf1938ab3cf810cf4e58de49e0af2a2b3b',
        'FPAU': '1.1.1337157123.1767345847',
        '_fbp': 'fb.2.1767345852994.56679767543448031',
        'cf_clearance': 'RCqvEM8C27eK95nSSXRqkznjdX7mNej8_qxYPPnv8TY-1769679400-1.2.1.1-0VO1z6v_puGhSRyWgeVFSCvOXlp04l2nH16rX6GDDLAt4goEh5lxABd4YcdwV78U04Xg8ks5NhqOGNTg026teeG4hlh_Jkxla3tkNr2gt0Enh30h.Wme.GXC3Fd9vKloUB8eTJtDBQvlW9Vh.OUtO.T1X6_gBcNYFY1c6NkKDaIKlKpXnWwzjeIVAeoR0klqOrGQlxD2hXMP45S3oezoYt1.TdLvw_Hv5ejUXdz6uCc',
        'FPLC': 'AAYeS%2F241XjuRfOuyaYF4LEfMwaqN%2BijxE2kc0bbx5ylKvlGIoQHdJjMORE5swS7rLqDuSAXIzBNot8zdCmsLHSVvaK%2FCNqwWlH8CI5dd1D%2BqPeRLw%2Bz3j%2F%2FpWv36A%3D%3D',
        'FPGSID': '1.1769679420.1769679420.G-T941HWW5GL.5gFhdS2VTBqKDDhQXogWEQ',
        'SESSION_TOKEN': 'eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9.eyJleHAiOjE3Njk3NjU4MjIsImlhdCI6MTc2OTY3OTQyMiwianRpIjoiYWVlNjA3NmEtY2M4NS00MmFiLTk5MGEtOTE0ZjViYzA3Y2ViIiwiYXVkIjoiODA2NjE1NzItOTE0MC00ZmU0LTljMmItNzEyMjkzYzI1MDBjIn0.DO9FHuNdSKOn2WTIWNMqXd2wyvOAumf9dOghODCL7QXokOLj16h8gqlfCGNE1AKGpmR-VuLGAp6bl1IoFPwMHMcylKfzLdgHyZpymH5rL8TCT-bkPwF9EbAHjg_759DUOlCTHCUXXoFLj1vocxVIzKFtZK9D3hor4EipEYvH4azTU9DggWLmw9aOa8B_oPUo7ytcX227zrWj5qclJgpRWSAf6XfwpNiVOT7fJUy53-XiCuAOxpal1vicwPALG4xBOxd7xkxbDBy8zh-8bBR58AQk2i6j4Vsy6qOGPhmgMOwT65VG9_zL337t5C1AOjOMqizTvZmqSGgj_UiuNId_oA',
        'YOUI_HIT_KEY': 'aee6076a-cc85-42ab-990a-914f5bc07ceb',
        'afx_profile_brid_da': '1',
        '_uetsid': '14525de0fcf611f0ba2b2dc6376c1bf5',
        '_uetvid': 'f21f03a0e76b11f09a42736e709424fe',
        'DC_UID': '0',
        '66907624-69d0-1ab8-e3c0-5c641ee0d18d': 'G6BEoSxZXzOkhC4BmwsQtw8cspM%3D%7CWU9VSV9RVU9URVM%3D',
        '66907624-69d0-1ab8-e3c0-5c641ee0d18d~': 'Nm6qEpqGWcF5XsNX1TtR3lme25ov4M3AqklAN0cHdTI%3D',
        'TS01dc4fc6': '01e66920c7e90a9c980ff74dd15d8068cd517009a73496ce2bf9d1a7c2ac7f1933b2c5dc5623e96fad9ad9ba0d5096cad4e1641331',
        'TS01dc4fc6': '01e66920c768f8cc4fd1e10a4de299e8b0c0fab1ad7b814a7611aab2fa635415afab65262f13f2b6a2a2316e15998195ea3b441db6',
    }
    return {**dict(response.cookies.items()), **cookies}

async def get_headers_playwright(url=None):
    PROXY_SERVER = "http://shp-watchmycompetitor-us-v00001.tp-ns.com:80"
    PROXY_USERNAME = "watchmycompetitor"#"customer-watchmycompetitor"
    PROXY_PASSWORD = "dmt276gnw845"#"DKo3FPU_qpNJ"

    username = 'wmc_user_aakFe'
    country = 'AU'

    PROXY_SERVER = "http://pr.oxylabs.io:7777"
    PROXY_USERNAME = f"customer-{username}-cc-{country}"
    PROXY_PASSWORD = "WmcLetmein_123"

    
    
    
    

    
    
    # Fresh flow id might be better than hardcoding, but keeping your sample URL:
    rehyderate = True
    if not url:
        rehyderate = False
        url = "https://quote.youi.com.au/createquote/oahomeplf/006210eb-9b7d-426d-8aa5-db4aa106cb8a?leadType=sf&leadSource=&placementKey=&promotionCode=000&promotionNumber=&promotionCodeWord="

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
                await page.screenshot(path="screenshot.png")

                cookies_list:list = await context.cookies()
                cookie_dict = {
                    cookie["name"]: cookie["value"] for cookie in cookies_list
                }

                match = re.search(r"/online/([a-zA-Z0-9-]+)", page.url)
                headers:dict ={
                    'X-NewRelic-ID': 'VQYFVFFaDBABVFJQBAcPXlUG',
                    'HQSudwpRPP-f': 'A10yHQmcAQAAZNe7SkZy-_1hDO10ty_cvD71oV4lnKVCj2kVxeNPZlPnEGPUAQ7J9V-cuI1HwH8AADQwAAAAAA==',
                    'HQSudwpRPP-b': '-4qgsxs',
                    'HQSudwpRPP-c': 'AMCRFgmcAQAAuOQbt12Pwt9QFNemtRH4hrypC7rMs4wEcypiR8ttwHxjPD7V',
                    'HQSudwpRPP-d': 'ADYAhICBAKGAgQGAAYAQgISigaIAwAGAyPpCxg_13ocRnsD_CLzTAMttwHxjPD7V_____6R7nDYCu1Wbaj_tVP8iVY4awiEdhQ',
                    'HQSudwpRPP-z': 'q',
                    'HQSudwpRPP-a': 'QewXL_bSne9YnOgJBB4BIEk5SKfb4-_TQuarA08Y7JI8rpDqkK=udKhDfMiL2Q8lq78vkDd1gyA3ySnltxm7oVOkbkHd7lPsTGEXIjuy1u58-C=2aYFGw0X9D-iiJ9QJBdpVAYjrfZ1Q5O61imLRjTw4v6OH91dU2Y8S2tRfuTa5HFWwU3SnM8VdpmR_31qdw8HXIwwWitq3BUQzOC--aesJZFlx5-CVlS9bBBqI7cx7ABJqboYCE=DzQ4VTPDKEpQPIiEaFkmU7rc_FPQZ6q2czwlIVaF_O4HEbezOpLsFeh0=mLZQbIZd7woulwylS7gPUhSDqnkQxeWkZvoB0Ox2fwqOLh4PqbmIqdY22XFcufHxEx1GaLT402-77FZSzqY4riWhDx4cXHmiGxEl3SxL=19Ir6omYvkkAyZh4PG_tV6T9rVJJm-4oCsnQc_CUHe171giA7UfBMHVJj=90OCp1IsmB-GXblKP6DiMFo5aDjYwoWxqLpk0hRr6CyhB4Dn=QBjtyXMlu=snko9-o7V==gGFTmMbk_4fXCr0rLf=4msD8uicK-SvhCPp_ZpRhXA4zZikTLWLcy-4wMOTrKgE33G1THyyYteFjhFryT6iMln8FQkxdc6C-bzLH8FepgL8=lEH0U2JDILa_lRidhwWSyvka==Yw2L3OYiXfZU-W=22Hu3cAzyTx36ZmfhyVJ03ZBLvEjV0TCekW7XOl_K7kYa0rIpvgfmFohAEzqBvaMgkjuf_Fj9gvSZyUpJnrYv6idAig9cU65ETFfS2jqt1VM22dCk7tgx=9PpnsL0yvbXbW4RhpIub0ZJz8LmCjlyoy_tb3yLvgqFKwbZLbY_3yzMePQRJEB=wI9s3ugm0C8um=KXrWpMOEtOsjmfP7I11QKGXcWtB5A0_BT9u0AejYqlItBgfSZJvfdusA7DE_0ZmZWbn87G6FRkxMcy2-q6G8Cpl8lVjHtCXA_iRtlPdkMpzwi9B7b6XA0ZwWscfTTcODwDD3kKFQ_C5KjoHuZdIRPJ3EJvrhjtcgOyMfpMJBD13LKo1WEUBklbRPP=cTiFJaw=2Rxc1Eys2XiL2hzLKal=C1axzmR_DAMJu1qyROO=1dZq67ZBmgHLclhmME96n71=lzXFdwdtzpcxFnOWIimk-c0BtUUQdYqcMIp8uk67s1a8f1ARRYPfjZKlI1l4qliwhVFC=4MyJ7TSkx23BM8Cd8KraPqXaFH7PQRzk-zsKGj0kxYQTMatSIIzsIa63BPKvYfgLiEtrrl435E2hdYWImFFsU2p9t8mpAA6_BF2SJxyQIH7noFYR34JTq2HYcmLAYhtx29bbBDB8yhZGz8exq0O0vq736kVH2rzKlGRfMyuvL2PdZm9LJT==qeiDw_9j-H4_6TXuIy8ncvedctOmey4iyhG65YO3XcMHcFM4COqJEJJMrXcZPHm3-_VqcwICBoM7L6yYTEmYjgc26XE8hHwBPom039zHPTUozSfpwvyR8mwvuaDKq9ia1r3Q2tWcJoXwDIvF2u5X2CeuABBLWyPbc-=k7ch9A-0YURnzLSXiWwlkUBJrPrIyddYkq008TTBUPM0MhTh3BivkKuF8lRO9fuYq5=L19ykCK02cJu=s1esMd2lF99Dz-HyDtCm=1KvotJCxtbVqROXAw=HESXzshBP4OXVUXtneZej=HtX7=kwGtxLgxuCEZkr8czvkXYzmZAe14BDo3x0c7zEVn_XEM=wj=yaScvnhCZY_1XkarP7qauO6=Gp5ha2FVvnsQI81oqCD5S5gxXfZ11mSrosLyTur-3xJPjlSCFHBd2kYRMJWbzUqhaGHD5576Br=rYCjz00cFFaZI4Gf7DVPlmOZBE9q92ZUFEu=Y=6mYolaCrgKen1tKCkaVU9OZSeBtuyfJITp-EUiCEepk8IZDnmEWQi=yXWwEMXWo2bbvIUW1yQLyhfVjPqgqxaLmIbF2nU4U168tgUOFxay4xLhpBIza3Todrso0SVR_ZelxcD4J_5VxowZz96CrBBUyK8_=cuL8aPAMQWwn1eA1twVyinRDSvKIc4kDbD-p9AW4O0VysujOWhvBH5JhBB5obPtvuHlOH5aszm=F7AiKTx=PZzvC=VPhBKaGAQ1FxevzgMgntSr8J_sMAf6lJF1mDjqfvXL5ap==r0f_-xTyuLPR0wz7pPu7MRKu52AHVULYwE-KJYcisDTmIRMWQY2UqVLrcGRYotSUmkyd-XWIkOzcbO2_bV19UwQhRUVJVPv8GAk7btrmEc1AgBEt4xFAZAK9KsSojcPdl-GyJOXAag0nod8kwg=Ie1LJUgAJXceW4S-mdcGu3oPdv=UgjwRgO4UgBeWDZoIuX99oL0f6k9swuO-Jn7zvhkV6HJD2Yo4M7w4DEwV7yccFq4BWLceK1MPbOLOpdkCFrUHO2TGnuX3HYY53jd4bOJcjB=yUleg3PO5Zvf7qGQEXcuLgeL9BBWqZ3hIfqLA-wLgTIEQ3OkMcX9ZUxlHAm74_ZmBaxVHStxVlzDj36UiCUHw=QROrbuB2OdY8xlJQdi_jDezXQxuJwGZb2gvKaZ1lPtOSKfHjIf3naWvYohnqJu2=ISzAQXkPskKAdLClP_C0RL5MdRkJ3SkJ7zzQ6RCzUy1hDkcjYcg9K9gXI-0AxWBxrD7pOIZu1O=B7LdLCueh5vLYy28EXfvmO5GzfH3cduVVjeA=dyYl1dHryT58OeG_6iQi00EKa4an8T1gqXsW',
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
                        flowInstanceSession = f"oahomeplf-{flowInstanceId}"

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

                await browser.close()

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
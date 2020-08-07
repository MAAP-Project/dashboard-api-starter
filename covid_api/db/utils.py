"""Db tools."""

import boto3
import csv
import json
import re
from datetime import datetime
from typing import Dict, List, Optional

from covid_api.core.config import INDICATOR_BUCKET, DT_FORMAT, MT_FORMAT
from covid_api.models.static import IndicatorObservation

s3 = boto3.client("s3")


def gather_s3_keys(
    spotlight_id: Optional[str] = None,
    spotlight_name: Optional[str] = None,
    prefix: Optional[str] = None,
):
    """
    Returns a set of S3 keys. If no args are provided, the keys will represent
    the entire S3 bucket.
    Params:
    -------
    spotlight_id (Optional[str]):
        Id of a spotlight to filter keys by; must be used in conjunction with
        `spotlight_name`
    spotlight_name (Optional[str]):
        Human readable label of a spotlight to filter keys by; must be used in
        conjunction with `spotlight_id`. This field is necessary because some
        datasts contain spotlight labels in their file nameing coventions
        instead of spotlight ids (ex: BM_500M_DAILY)
    prefix (Optional[str]):
        S3 Prefix under which to gather keys, used to specifcy a specific
        dataset folder to search within.

    Returns:
    -------
    set(str)

    """
    keys: set = set()

    list_objects_args = dict(Bucket=INDICATOR_BUCKET)

    if prefix:
        list_objects_args["Prefix"] = prefix

    response = s3.list_objects_v2(**list_objects_args)
    keys.update({x["Key"] for x in response.get("Contents", [])})

    while response["IsTruncated"]:

        list_objects_args["ContinuationToken"] = response["NextContinuationToken"]
        response = s3.list_objects_v2(**list_objects_args)

        keys.update({x["Key"] for x in response.get("Contents", [])})

    if not spotlight_id and not spotlight_name:
        return keys

    return {
        key
        for key in keys
        if re.search(
            rf"""[^a-zA-Z0-9]({spotlight_id}|"""
            rf"""{spotlight_name.replace(' ', '')})[^a-zA-Z0-9]""",
            key,
            re.IGNORECASE,
        )
    }


def get_dataset_folders_by_spotlight(spotlight_id: str, spotlight_name: str):
    """
    Returns the S3 prefix of datasets containing files for the given spotlight

    Params:
    ------
    spotlight_id (str): id of spotlight to search for
    spotlight_name (str): human readable label of spotlight to search for

    Returns:
    --------
    set(str)
    """
    folders_matched: set = set()

    keys = gather_s3_keys(spotlight_id, spotlight_name)
    itercount = 0
    while len(keys):
        itercount += 1
        key = keys.pop()
        if key.split("/")[0] not in folders_matched:
            folders_matched.add(key.split("/")[0])

            # Once a folder has been found to contain the given spotlight
            # all other files for that folder no longer have to be searched
            keys = {k for k in keys if not k.startswith(key.split("/")[0])}

    return folders_matched


def get_dataset_domain(
    dataset_folder: str,
    time_unit: Optional[str] = None,
    spotlight: Optional[Dict] = None,
):
    """
    Returns a domain for a given dataset as identified by a folder. If a
    time_unit is passed as a function parameter, the function will assume
    that the domain is periodic and with only return the min/max dates,
    otherwise ALL dates available for that dataset/spotlight will be returned.

    Params:
    ------
    dataset_folder (str): dataset folder to search within
    time_unit (Optional[str]): time_unit (hardcoded) with the dataset
        metadata json files
    spotlight (optional[Dict[str,str]]):
        a dictionary containing the `spotlight_id` and `spotlight_name` of
        a spotlight whose domain should be returned.

    Return:
    ------
    List[datetime]
    """
    s3_keys_args = dict(prefix=dataset_folder)
    if spotlight:
        s3_keys_args.update(spotlight)

    keys = gather_s3_keys(**s3_keys_args)
    dates = []
    for key in keys:
        result = re.search(
            # matches either dates like: YYYYMM or YYYY-mm-dd (with any
            # non-alphanumeric delimiter)
            r"""[^a-zA-Z0-9]((?P<MT_DATE>(\d{6}))|"""
            r"""((?P<YEAR>\d{4})[^a-zA-Z0-9](?P<MONTH>\d{2})[^a-zA-Z0-9]"""
            r"""(?P<DAY>\d{2})))[^a-zA-Z0-9]""",
            key,
            re.IGNORECASE,
        )
        if not result:
            continue

        date = None
        try:
            if result.group("MT_DATE"):
                date = datetime.strptime(result.group("MT_DATE"), MT_FORMAT)
            else:
                datestring = (
                    f"""{result.group('YEAR')}-{result.group('MONTH')}"""
                    f"""-{result.group('DAY')}"""
                )
                date = datetime.strptime(datestring, DT_FORMAT)
        except ValueError:
            # Invalid date value matched
            continue

        dates.append(date)

    if time_unit and len(dates):
        return [min(dates), max(dates)]

    return dates


def s3_get(bucket: str, key: str):
    """Get AWS S3 Object."""
    response = s3.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def get_indicator_site_metadata(identifier: str, folder: str) -> Dict:
    """Get Indicator metadata for a specific site."""
    try:
        key = f"indicators/{folder}/{identifier}.json"
        return json.loads(s3_get(INDICATOR_BUCKET, key))
    except Exception:
        return {}


def indicator_folders() -> List:
    """Get Indicator folders."""
    response = s3.list_objects_v2(
        Bucket=INDICATOR_BUCKET, Prefix="indicators/", Delimiter="/",
    )
    return [obj["Prefix"].split("/")[1] for obj in response["CommonPrefixes"]]


def indicator_exists(identifier: str, indicator: str):
    """Check if an indicator exists for a site"""
    try:
        s3.head_object(
            Bucket=INDICATOR_BUCKET, Key=f"indicators/{indicator}/{identifier}.csv",
        )
        return True
    except Exception:
        try:
            s3.head_object(
                Bucket=INDICATOR_BUCKET,
                Key=f"indicators/{indicator}/{identifier}.json",
            )
            return True
        except Exception:
            return False


def get_indicators(identifier) -> List:
    """Return indicators info."""
    indicators = []
    for folder in indicator_folders():
        if indicator_exists(identifier, folder):
            indicator = dict(id=folder)
            try:
                data = []
                # metadata for reading the data and converting to a consistent format
                metadata_json = s3_get(
                    INDICATOR_BUCKET, f"indicators/{folder}/metadata.json"
                )
                metadata_dict = json.loads(metadata_json.decode("utf-8"))

                # read the actual indicator data
                indicator_csv = s3_get(
                    INDICATOR_BUCKET, f"indicators/{folder}/{identifier}.csv"
                )
                indicator_lines = indicator_csv.decode("utf-8").split("\n")
                reader = csv.DictReader(indicator_lines,)

                # top level metadata is added directly to the response
                top_level_fields = {
                    k: v for k, v in metadata_dict.items() if isinstance(v, str)
                }

                # for each row (observation), format the data correctly
                for row in reader:
                    date = datetime.strptime(
                        row[metadata_dict["date"]["column"]],
                        metadata_dict["date"]["format"],
                    ).strftime(DT_FORMAT)

                    other_fields = {
                        k: row.get(v["column"], None)
                        for k, v in metadata_dict.items()
                        if isinstance(v, dict) and v.get("column") and k != "date"
                    }

                    # validate and parse the row
                    i = IndicatorObservation(**other_fields)

                    data.append(dict(date=date, **i.dict(exclude_none=True)))

                # add to the indicator dictionary
                indicator["domain"] = dict(
                    date=[
                        min(
                            data, key=lambda x: datetime.strptime(x["date"], DT_FORMAT),
                        )["date"],
                        max(
                            data, key=lambda x: datetime.strptime(x["date"], DT_FORMAT),
                        )["date"],
                    ],
                    indicator=[
                        min(data, key=lambda x: x["indicator"])["indicator"],
                        max(data, key=lambda x: x["indicator"])["indicator"],
                    ],
                )
                indicator["data"] = data
                indicator.update(top_level_fields)

            except Exception as e:
                print(e)
                pass

            try:
                site_metadata = get_indicator_site_metadata(identifier, folder)
                # this will, intentionally, overwrite the name from the data if present
                if "name" in site_metadata:
                    indicator["name"] = site_metadata.get("name")
                indicator["notes"] = site_metadata.get("notes", None)
                indicator["highlight_bands"] = site_metadata.get(
                    "highlight_bands", None
                )
            except Exception as e:
                print(e)
                pass

            indicators.append(indicator)

    return indicators

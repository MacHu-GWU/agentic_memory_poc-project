# -*- coding: utf-8 -*-

"""
https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore.html
"""

import boto3

boto_ses = boto3.Session(profile_name="esc_app_dev_us_east_1")
client = boto3.client("bedrock-agentcore")

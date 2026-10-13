"""Explicitly approximate token counts; never claim an upstream tokenizer."""
import json
import math
import re


def input_tokens(payload):
    content={key:payload[key] for key in ('messages','input','instructions','system','tools') if key in payload}
    text=json.dumps(content,ensure_ascii=False,separators=(',',':'))
    wide=len(re.findall(r'[\u2e80-\u9fff\uac00-\ud7af\uf900-\ufaff]',text))
    return max(1,wide+math.ceil((len(text)-wide)/4)+8)

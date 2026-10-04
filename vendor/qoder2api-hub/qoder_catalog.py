"""Official per-realm model catalog for the Qoder gateway — FULL FIDELITY.

数据来源（完全跟官方走）：本机官方客户端模型目录缓存解密快照
  国际版  ~/.qoder/.models/<uid>/catalog-v6   （QMC v1: HKDF-SHA256(uid)+AES-256-GCM）
  国内版  ~/.qoder-cn/.models/<uid>/catalog-v6
取 chat 场景、**逐字段原样保留**（含 promotion 峰谷价、context_config 多窗口、
thinking_config 思考档位、is_free/is_new/icon、strategies 等）。

快照存放：运行时优先读取同目录的 qoder_catalog_intl.json / qoder_catalog_cn.json
（客户端更新后用 `python _refresh_catalog.py` 一条命令重新导出，会打印差异摘要）；
两个文件缺失时才回退到本文件中内嵌的**冻结副本**（并打印 WARNING）。

条目字段（官方原样）：
  key                 上游模型 key（缩写 id）
  display_name        官方模型名（主流名，如 Qwen3.8-Max / GLM-5.2）
  enable              该账号/套餐当前是否开放（展示为徽章，**不过滤**）
  max_input_tokens    默认上下文
  context_config      可选窗口 {label:{token_count,is_default}}（200K/400K/1M）
  thinking_config     思考开关与档位 {enabled:{efforts:{low..xhigh,is_default}},disabled}
  price_factor        当前（谷时/促销）计费倍率
  promotion           峰谷价 {active,badge,description,window_start,window_end,
                        discount_factor,before_promotion_price_factor,timezone}
  original_price_factor / is_free / is_new / icon / is_vl / is_reasoning ...

运行时优先级：网关动态 /algo/api/v2/model/list > 本机官方目录缓存 > 本快照。
双区清单**不同**（intl 独占 {'efficient', 'cmodel', 'smodel', 'ultimate', 'performance'}，cn 独占 {'q37fmodel', 'gm51model'}）。

三源差异（实测 · **这不是 bug，是数据源不同**）：
  同一台机器上可能同时存在三套"官方数据"，它们的抓取时刻不同，字段取值因此
  互不相同；网关按「动态接口 > 本机缓存 > 本快照」降级，降级到哪一级就换一套口径：
    1) 动态接口 /algo/api/v2/model/list —— 官方"此刻"（桌面版选择器同源）
    2) 本机缓存 ~/.qoder[.cn]/.models/<uid>/catalog-v6 —— 客户端上次联网同步时刻
    3) 本模块 qoder_catalog_intl.json / qoder_catalog_cn.json（外部双区 JSON）；
       文件缺失/不可读时回退本文件内嵌的**冻结副本**（更旧）并打印 WARNING
  实测样例（2026-09-29 快照 vs 2026-10-02 客户端缓存）：
    qmodel_38max   price_factor 内嵌 0.2 / 外部 JSON 0.5 / 本机缓存 0.2，
                   且内嵌 promotion.active=true 而外部 JSON 为 false
    qmodel_latest  内嵌带 promotion（0.1 折后价），本机缓存无 promotion
    其余已观测差异字段：is_sensitive、max_input_tokens、minimal_version、
      thinking_config 的默认档（is_default 落在 low 还是 medium）
  影响面：qoder_proxy.model_entry 的谷时价严格由 promotion 推导
    （price_factor_valley = before_promotion_price_factor × discount_factor），
    所以**降级会直接改变下游看到的峰谷价**，而不是"大概差不多"。
  观察点：降级时打印 [qoder-catalog] WARNING（既有行为，未改动）；
    另提供只读来源标注 snapshot_source(realm) / snapshot_report()（本期新增）。
"""

import json
import os

# 国际版 (qoder.com / api1.qoder.sh) 官方 chat 场景全字段快照（冻结回退副本；
# 运行时优先读取同目录 qoder_catalog_intl.json / qoder_catalog_cn.json）
_INTL_JSON = r'''
[
  {
    "key": "auto",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Auto",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "price_factor": 1.0,
    "max_input_tokens": 200000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ]
  },
  {
    "key": "ultimate",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Ultimate",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "price_factor": 1.6,
    "max_input_tokens": 1000000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "xhigh": {},
          "high": {
            "is_default": true
          },
          "low": {},
          "max": {},
          "medium": {}
        },
        "is_default": true
      }
    }
  },
  {
    "key": "performance",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Performance",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "price_factor": 1.1,
    "max_input_tokens": 1000000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "272K": {
        "token_count": 272000,
        "is_default": true
      },
      "1M": {
        "token_count": 1000000
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "xhigh": {
            "description": "Extreme thinking intensity"
          },
          "high": {
            "description": "High thinking intensity"
          },
          "low": {
            "description": "Low thinking intensity"
          },
          "max": {
            "description": "Maximum thinking intensity"
          },
          "medium": {
            "description": "Medium thinking intensity",
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "efficient",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Efficient",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "price_factor": 0.3,
    "max_input_tokens": 200000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ]
  },
  {
    "key": "smodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Sonus",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 3.2,
    "max_input_tokens": 180000,
    "minimal_version": {
      "cli": "1.0.48",
      "jb_plugin": "2026.715.1",
      "ide": "1.13.3",
      "qoder_wake": "1.0.48"
    },
    "strategies": [
      {
        "tag": "C4",
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "xhigh": {},
          "high": {
            "is_default": true
          },
          "low": {},
          "max": {},
          "medium": {}
        },
        "is_default": true
      }
    }
  },
  {
    "key": "cmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Cantus",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 3.2,
    "max_input_tokens": 200000,
    "minimal_version": {
      "cli": "1.0.48",
      "jb_plugin": "2026.715.1",
      "ide": "1.13.3"
    },
    "strategies": [
      {
        "tag": "C4",
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "xhigh": {},
          "high": {
            "is_default": true
          },
          "low": {},
          "max": {},
          "medium": {}
        },
        "is_default": true
      }
    }
  },
  {
    "key": "qmodel_38max",
    "format": "openai",
    "source": "system",
    "enable": true,
    "display_name": "Qwen3.8-Max",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": true,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 180000,
    "is_free": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {},
      "enabled": {
        "efforts": {
          "xhigh": {},
          "low": {},
          "medium": {
            "is_default": true
          }
        },
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 60% off",
        "zh": "错峰 4 折"
      },
      "description": {
        "en": "Off-Peak 60% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段4折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Singapore",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.4,
      "before_promotion_price_factor": 0.5,
      "link_url": {
        "en": "https://docs.qoder.com/events/offpeakrate",
        "zh": "https://docs.qoder.com/zh/events/offpeakrate"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "qfmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "display_name": "Qwen3.8-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.0,
    "original_price_factor": 0.1,
    "max_input_tokens": 180000,
    "is_free": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {},
      "enabled": {
        "efforts": {
          "xhigh": {},
          "low": {},
          "medium": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "qmodel_latest",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Qwen3.7-Max",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.1,
    "original_price_factor": 0.5,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "cli": "0.1.25",
      "jb_plugin": "0.15.0",
      "ide": "0.3.0"
    },
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 80% off",
        "zh": "错峰2折"
      },
      "description": {
        "en": "Off-Peak 80% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段2折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Singapore",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.2,
      "before_promotion_price_factor": 0.5,
      "link_url": {
        "en": "https://docs.qoder.com/events/offpeakrate",
        "zh": "https://docs.qoder.com/zh/events/offpeakrate"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "qmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Qwen3.7-Plus",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.04,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "cli": "0.1.25",
      "jb_plugin": "0.15.0",
      "ide": "0.3.0"
    },
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 60% off",
        "zh": "错峰4折"
      },
      "description": {
        "en": "Off-Peak 60% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段4折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Singapore",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.4,
      "before_promotion_price_factor": 0.1,
      "link_url": {
        "en": "https://docs.qoder.com/events/offpeakrate",
        "zh": "https://docs.qoder.com/zh/events/offpeakrate"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "kmodel_latest",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Kimi-K3",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.8,
    "max_input_tokens": 180000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "kmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "Kimi-K2.8-Preview",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.3,
    "minimal_version": {
      "cli": "0.1.26",
      "jb_plugin": "0.12.0",
      "ide": "0.3.1"
    },
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_sensitive": true,
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "gmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "GLM-5.3",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.6,
    "max_input_tokens": 180000,
    "strategies": [
      {
        "tag": "C4",
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_sensitive": true,
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "gfmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "GLM-5.3-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.1,
    "max_input_tokens": 1000000,
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "high": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "dmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "DeepSeek-V4-Pro",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.8,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "cli": "0.1.25",
      "jb_plugin": "0.15.0",
      "ide": "0.3.0"
    },
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {
            "description": "High thinking intensity"
          },
          "max": {
            "description": "Maximum thinking intensity",
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "dfmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "DeepSeek-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "cli": "0.1.25",
      "jb_plugin": "0.15.0",
      "ide": "0.3.0"
    },
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {},
          "max": {
            "is_default": true
          },
          "low": {}
        },
        "is_default": true
      }
    }
  },
  {
    "key": "mmodel",
    "format": "openai",
    "source": "system",
    "enable": false,
    "display_name": "MiniMax-M3",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "cli": "0.1.26",
      "jb_plugin": "0.12.0",
      "ide": "0.3.1"
    },
    "strategies": [
      {
        "tag": "C4",
        "priority": 999,
        "enabled": false,
        "disabled_message_key": "codeSafeModelReason"
      }
    ],
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    }
  }
]
'''

# 国内版 (qoder.com.cn / gateway.qoder.com.cn) 官方 chat 场景全字段快照
_CN_JSON = r'''
[
  {
    "key": "auto",
    "format": "openai",
    "source": "system",
    "enable": true,
    "display_name": "Auto",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": true,
    "price_factor": 0.5,
    "max_input_tokens": 180000,
    "is_sensitive": true
  },
  {
    "key": "qmodel_38max",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderQwenAiFill",
    "display_name": "Qwen3.8-Max",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_free": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {},
      "enabled": {
        "efforts": {
          "xhigh": {},
          "low": {},
          "medium": {
            "is_default": true
          }
        },
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 60% off",
        "zh": "错峰 4 折"
      },
      "description": {
        "en": "Off-Peak 60% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段4折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Shanghai",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.4,
      "before_promotion_price_factor": 0.5,
      "link_url": {
        "zh": "https://docs.qoder.cn/product-overview/qwen-3-7-series-model-staggering-discount"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "qfmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderQwenAiFill",
    "display_name": "Qwen3.8-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.0,
    "original_price_factor": 0.1,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_free": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {},
      "enabled": {
        "efforts": {
          "xhigh": {},
          "low": {},
          "medium": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "qmodel_latest",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderQwenAiFill",
    "display_name": "Qwen3.7-Max",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.1,
    "max_input_tokens": 180000,
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 80% off",
        "zh": "错峰2折"
      },
      "description": {
        "en": "Off-Peak 80% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段2折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Shanghai",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.2,
      "before_promotion_price_factor": 0.5,
      "link_url": {
        "zh": "https://docs.qoder.cn/product-overview/qwen-3-7-series-model-staggering-discount"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "qmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderQwenAiFill",
    "display_name": "Qwen3.7-Plus",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.04,
    "max_input_tokens": 180000,
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "is_default": true
      }
    },
    "promotion": {
      "active": true,
      "badge": {
        "en": "Off-Peak 60% off",
        "zh": "错峰4折"
      },
      "description": {
        "en": "Off-Peak 60% off (10 PM-8 AM UTC+8)",
        "zh": "错峰时段4折优惠（10 PM-8 AM UTC+8）"
      },
      "timezone": "Asia/Shanghai",
      "rule_id": "idle_time_model_credit_discount",
      "discount_factor": 0.4,
      "before_promotion_price_factor": 0.1,
      "link_url": {
        "zh": "https://docs.qoder.cn/product-overview/qwen-3-7-series-model-staggering-discount"
      },
      "window_start": "22:00",
      "window_end": "08:00"
    }
  },
  {
    "key": "q37fmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderQwenAiFill",
    "display_name": "Qwen3.7-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.1,
    "max_input_tokens": 180000,
    "is_sensitive": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    }
  },
  {
    "key": "dmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderDeepseekFill",
    "display_name": "DeepSeek-V4-Pro",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.8,
    "max_input_tokens": 96000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {
            "description": "High thinking intensity"
          },
          "max": {
            "description": "Maximum thinking intensity",
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "dfmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderDeepseekFill",
    "display_name": "DeepSeek-Flash",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {
            "description": "High thinking intensity"
          },
          "max": {
            "description": "Maximum thinking intensity",
            "is_default": true
          },
          "low": {}
        },
        "is_default": true
      }
    }
  },
  {
    "key": "gmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderZhipuAiFill",
    "display_name": "GLM-5.3",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.6,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "gfmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "display_name": "GLM-5.3-Flash",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.1,
    "max_input_tokens": 1000000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "efforts": {
          "high": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "gm51model",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderZhipuAiFill",
    "display_name": "GLM-5.2",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.6,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "disabled": {
        "description": "Disable thinking"
      },
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {
            "description": "High thinking intensity"
          },
          "max": {
            "description": "Maximum thinking intensity",
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "kmodel_latest",
    "format": "openai",
    "source": "system",
    "enable": true,
    "display_name": "Kimi-K3",
    "is_vl": true,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.8,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_free": false,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "kmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderKimiFill",
    "display_name": "Kimi-K2.8-Preview",
    "is_vl": true,
    "is_reasoning": true,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.3,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_sensitive": true,
    "is_editable": true,
    "context_config": {
      "1M": {
        "token_count": 1000000
      },
      "200K": {
        "token_count": 200000,
        "is_default": true
      },
      "400K": {
        "token_count": 400000
      }
    },
    "thinking_config": {
      "enabled": {
        "description": "Enable thinking",
        "efforts": {
          "high": {},
          "low": {},
          "max": {
            "is_default": true
          }
        },
        "is_default": true
      }
    }
  },
  {
    "key": "mmodel",
    "format": "openai",
    "source": "system",
    "enable": true,
    "icon": "QoderMinimaxFill",
    "display_name": "MiniMax-M2.7",
    "is_vl": false,
    "is_reasoning": false,
    "is_default": false,
    "is_new": true,
    "price_factor": 0.2,
    "max_input_tokens": 180000,
    "minimal_version": {
      "vsc": "999.999.999"
    },
    "is_sensitive": true,
    "context_config": {
      "200K": {
        "token_count": 200000,
        "is_default": true
      }
    }
  }
]
'''

# ---------------------------------------------------------------------------
# 模型快照来源标注（只读观测面 · 纯新增：不改任何既有字段与语义）
#
# 目的：让"当前模型数据来自哪一级源"可被观察（三源差异见模块 docstring 同名章节）。
#   external-json   同目录 qoder_catalog_intl.json / qoder_catalog_cn.json
#   embedded-frozen 本文件内嵌冻结副本（外部文件缺失/不可读/非非空 list）
# 降级原因仅用于观察，不参与任何加载决策。
# ---------------------------------------------------------------------------
SNAPSHOT_SOURCE_EXTERNAL = "external-json"
SNAPSHOT_SOURCE_EMBEDDED = "embedded-frozen"

SNAPSHOT_DEGRADE_MISSING = "file-missing"
SNAPSHOT_DEGRADE_UNREADABLE = "unreadable-or-invalid"
SNAPSHOT_DEGRADE_EMPTY = "empty-or-not-list"

_SNAPSHOT_FILENAMES = {"intl": "qoder_catalog_intl.json",
                       "cn": "qoder_catalog_cn.json"}
_SNAPSHOT_SOURCES = {}          # filename -> SNAPSHOT_SOURCE_*
_SNAPSHOT_DEGRADE_REASONS = {}  # filename -> SNAPSHOT_DEGRADE_*


def _load_snapshot(filename, embedded):
    """加载区域目录快照：优先同目录 JSON 文件，缺失才回退内嵌冻结副本。

    外部文件由 `_refresh_catalog.py` 从本机官方客户端目录缓存重新导出（客户端
    更新后重跑一次即可），格式与内嵌副本完全一致（chat 场景逐字段原样）。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    reason = SNAPSHOT_DEGRADE_MISSING
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, list) and data:
            _SNAPSHOT_SOURCES[filename] = SNAPSHOT_SOURCE_EXTERNAL
            return data
        reason = SNAPSHOT_DEGRADE_EMPTY
    except FileNotFoundError:
        reason = SNAPSHOT_DEGRADE_MISSING
    except Exception:
        reason = SNAPSHOT_DEGRADE_UNREADABLE
    _warn("catalog snapshot %s missing/unreadable - falling back to the frozen "
          "built-in snapshot (may be outdated; run _refresh_catalog.py)" % filename)
    _SNAPSHOT_SOURCES[filename] = SNAPSHOT_SOURCE_EMBEDDED
    _SNAPSHOT_DEGRADE_REASONS[filename] = reason
    return json.loads(embedded)


def _warn(msg):
    try:
        print("[qoder-catalog] WARNING: %s" % msg)
    except Exception:
        pass


STATIC_INTL_MODELS = _load_snapshot("qoder_catalog_intl.json", _INTL_JSON)
STATIC_CN_MODELS = _load_snapshot("qoder_catalog_cn.json", _CN_JSON)
STATIC_MODELS = STATIC_CN_MODELS   # 兼容旧名

# 区域独占模型（官方双区清单差集）
INTL_EXCLUSIVE = {'efficient', 'cmodel', 'smodel', 'ultimate', 'performance'}
CN_EXCLUSIVE = {'q37fmodel', 'gm51model'}
INTL_EXCLUSIVE_PREFIXES = ("ultimate", "performance", "efficient", "smodel", "cmodel")
CN_EXCLUSIVE_PREFIXES = ("q37fmodel", "gm51model")


def models_for_realm(realm):
    """返回该区域的官方模型清单（list[dict]，全字段）。"""
    return STATIC_INTL_MODELS if realm == "intl" else STATIC_CN_MODELS


def display_names(realm):
    return {m["key"]: m.get("display_name") or m["key"]
            for m in models_for_realm(realm)}


def display_id(entry):
    """对外展示 id：「上游 key (官方模型名)」，与主流模型名直接对得上。"""
    key = entry.get("key") or entry.get("id") or ""
    name = entry.get("display_name") or entry.get("name") or ""
    if name and name != key:
        return "%s (%s)" % (key, name)
    return key


def format_model_id(key, realm=None):
    for m in (models_for_realm(realm) if realm
              else STATIC_CN_MODELS + STATIC_INTL_MODELS):
        if m.get("key") == key:
            return display_id(m)
    return key


# 客户端友好名 / 官方显示名 -> 上游 model key（双区同名 key 按显示名归一）
MODEL_ALIASES = {
    # Qwen
    "auto": "auto", "qoder-auto": "auto",
    "qwen3.8-max": "qmodel_38max",
    "qwen3.8-max-preview": "qmodel_38max",
    "qmodel_preview": "qmodel_38max",          # 旧 key -> 新 key
    "qwen3.8-flash": "qfmodel",
    "qwen3.7-max": "qmodel_latest",
    "qwen3.7-plus": "qmodel",
    "qwen3.7-flash": "q37fmodel",
    "qwen3.6-flash": "q36fmodel",
    # DeepSeek
    "deepseek-v4-pro": "dmodel",
    "deepseek-v4-flash": "dfmodel",
    "deepseek-flash": "dfmodel",
    # GLM
    "glm-5.3": "gmodel",
    "glm-5.3-flash": "gfmodel",
    "glm-5.2": "gm51model",
    # Kimi
    "kimi-k3": "kmodel_latest",
    "kimi-k2.8-preview": "kmodel",
    "kimi-k2.7": "kmodel",
    "kimi-k2.7-code": "kmodel",
    # MiniMax（双区同 key 不同型号，按区域清单解析）
    "minimax-m3": "mmodel",
    "minimax-m2.7": "mmodel",
    "minimax-m2.5": "mmodel",
}


def _display_name_index():
    idx = {}
    for m in STATIC_CN_MODELS + STATIC_INTL_MODELS:
        name = (m.get("display_name") or "").strip().lower()
        if name:
            idx.setdefault(name, m["key"])
    # 官方本地化名（zh.label）在 resolve_upstream_key 内惰性查询
    # （_EMBEDDED_MODEL_TEXT 定义在本函数之后，构造期不可引用）
    return idx


_DISPLAY_INDEX = _display_name_index()


def resolve_upstream_key(model, realm=None):
    """把客户端请求的模型名解析为上游 model key。

    接受：官方 key、展示 id「key (Name)」、人类可读别名、官方显示名
    （不分大小写）、官方本地化名（zh.label，如 Kimi-K2.7-Code / 极致）。
    """
    if not model:
        return "auto"
    m = str(model).strip()
    keys = {x["key"] for x in models_for_realm(realm)} if realm else None

    # 展示 id 形式: "qmodel_38max (Qwen3.8-Max)"
    if " (" in m and m.endswith(")"):
        head = m.rsplit(" (", 1)[0].strip()
        if head:
            m_key = head
        else:
            m_key = m
    else:
        m_key = m

    if keys and m_key in keys:
        return m_key
    low = m_key.lower()
    if m_key in MODEL_ALIASES:
        return MODEL_ALIASES[m_key]
    if low in MODEL_ALIASES:
        return MODEL_ALIASES[low]
    # 官方显示名（display_name / 括号内名）
    tail = low.rsplit(" (", 1)[-1].rstrip(")")
    if tail in _DISPLAY_INDEX:
        return _DISPLAY_INDEX[tail]
    if low in _DISPLAY_INDEX:
        return _DISPLAY_INDEX[low]
    # 官方本地化名（zh.label）——惰性查询官方文案资源
    try:
        labels = load_official_text().get("labels") or {}
        target = tail if tail != low else low
        for lk, lv in labels.items():
            if lk and (lv or "").strip().lower() == target:
                if (not keys) or lk in keys or \
                        lk in {x["key"] for x in STATIC_CN_MODELS + STATIC_INTL_MODELS}:
                    return lk
    except Exception:
        pass
    if keys and m_key in {x["key"] for x in STATIC_INTL_MODELS + STATIC_CN_MODELS}:
        return m_key          # 官方 key 但非本区域（跨区由独占校验拦截）
    if "/" in low:
        part = low.rsplit("/", 1)[-1]
        if part in MODEL_ALIASES:
            return MODEL_ALIASES[part]
        if part in _DISPLAY_INDEX:
            return _DISPLAY_INDEX[part]
    return m_key


def known_client_names():
    """所有可被 /v1/models 接受的名字（key / 展示 id / 别名 / 显示名）。"""
    out = set()
    for m in STATIC_INTL_MODELS + STATIC_CN_MODELS:
        out.add(m["key"])
        out.add(display_id(m))
        if m.get("display_name"):
            out.add(m["display_name"])
    out.update(MODEL_ALIASES.keys())
    return out


# ---------------------------------------------------------------------------
# 官方界面文案（与桌面版选择器一致）
#
# 来源：客户端安装目录 dynamic-text/qoder[-cn].json（zh.*）
#   zh.model.{key}.detail -> 模型介绍（能力清单副文案）
#   zh.model.{key}.label  -> 本地化显示名（如 Ultimate -> 极致；旧文案
#       Kimi-K2.7-Code 等按官方原样保留）
# 运行时优先实时读取客户端资源（客户端更新自动同步），失败回退内嵌快照。
# ---------------------------------------------------------------------------
_EMBEDDED_MODEL_TEXT = json.loads('{"descriptions": {"auto": "智能选择最适合的模型，平衡性能与成本", "lite": "基础推理能力，免费使用（高峰期可能响应较慢）", "cmodel": "尝鲜体验全球顶级模型，擅长超长自主任务执行", "dmodel": "深度求索正式版模型（DeepSeek-V4-Pro-0813），Agent 能力、世界知识与推理性能全面领先。", "gmodel": "智谱旗舰模型，擅长复杂系统工程与长程任务", "kmodel": "专为长上下文编程打造：精准遵循指令，可靠执行长链路任务", "mmodel": "原生多模态感知、前沿编码能力与 1M 上下文，驾驭高复杂度工作流", "qmodel": "千问旗舰模型，增强推理和智能体能力，擅长编程与复杂问题解决", "dfmodel": "深度求索正式版模型（DeepSeek-V4-Flash-0731），Agent 能力、世界知识与推理性能全面领先。", "gfmodel": "智谱全新原生多模态模型，深度理解图像与视频，自主完成研究分析、文档制作等复杂任务", "qfmodel": "千问开源权重的多模态 MoE 模型，在能力、延迟与成本间取得出色平衡", "ultimate": "专家级深度推理与思考能力，极致输出质量。", "efficient": "标准推理能力，高性价比", "performance": "高级推理能力，高质量输出", "qmodel_38max": "千问最新一代基座模型，2.4 万亿参数，在代码工程、专业办公、深度推理等核心场景全面领先", "kmodel_latest": "Kimi 迄今最强模型：2.8 万亿参数，面向软件工程、知识工作与深度推理而生", "qmodel_latest": "千问旗舰模型，具备顶尖智能体执行能力，可自主完成长达 35 小时的复杂任务"}, "labels": {"auto": "Auto", "lite": "轻量", "cmodel": "Cantus", "dmodel": "DeepSeek-V4-Pro", "gmodel": "GLM-5.3", "kmodel": "Kimi-K2.7-Code", "mmodel": "MiniMax-M3", "qmodel": "Qwen3.7-Plus", "dfmodel": "DeepSeek-V4-Flash", "gfmodel": "GLM-5.3-Flash", "qfmodel": "Qwen3.8-Flash", "ultimate": "极致", "efficient": "经济", "performance": "性能", "qmodel_38max": "Qwen3.8-Max", "kmodel_latest": "Kimi-K3", "qmodel_latest": "Qwen3.7-Max"}}')


# 国际版未开通套餐时的官方禁用提示（官方桌面版原文）
OFFICIAL_DISABLED_REASON = "需要升级或购买千问官方套餐开放"

_official_text_cache = {}


def load_official_text():
    """读取官方客户端 dynamic-text 文案 -> {"descriptions","labels"}（zh）。"""
    global _official_text_cache
    if _official_text_cache:
        return _official_text_cache
    import os as _os
    home = _os.path.expanduser("~")
    basename = _os.path.basename(home)
    real = []
    for drive in ("C", "D", "E", "F"):
        for prog, fname in (("Qoder CN", "qoder-cn.json"), ("Qoder", "qoder.json")):
            p = _os.path.join(f"{drive}:", "Users", basename, "AppData", "Local",
                              "Programs", prog, "resources", "dynamic-text", fname)
            if _os.path.isfile(p):
                real.append(p)
    for p in real:
        try:
            flat = {}

            def _walk(node, prefix=""):
                if isinstance(node, dict):
                    for k, v in node.items():
                        _walk(v, (prefix + "." + k) if prefix else k)
                elif isinstance(node, str):
                    flat[prefix] = node

            with open(p, encoding="utf-8") as fh:
                _walk(json.load(fh))
            d, l = {}, {}
            for k, v in flat.items():
                parts = k.split(".")
                if len(parts) >= 4 and parts[0] == "zh" and parts[1] == "model":
                    if parts[3] == "detail":
                        d[parts[2]] = v
                    elif parts[3] == "label":
                        l[parts[2]] = v
            if d:
                _official_text_cache = {"descriptions": d, "labels": l}
                return _official_text_cache
        except Exception:
            continue
    _official_text_cache = dict(_EMBEDDED_MODEL_TEXT)
    return _official_text_cache


def official_description(key):
    """官方模型介绍（zh.detail），无则空串。"""
    return load_official_text()["descriptions"].get(key) or ""


def official_local_name(key):
    """官方本地化显示名（zh.label）；与 display_name 相同时返回空串。"""
    lab = load_official_text()["labels"].get(key)
    if not lab:
        return ""
    for m in STATIC_CN_MODELS + STATIC_INTL_MODELS:
        if m["key"] == key:
            return "" if lab == m.get("display_name") else lab
    return lab


# ---------------------------------------------------------------------------
# 只读观测面：当前快照来源（供 /v1/models 等对外接口与诊断复用）
#
# 纯查询接口，不改变任何既有语义。三源差异的实测数据见模块 docstring。
# 对外接线示例（一行，由 qoder_proxy 侧负责；本模块不反向依赖它）：
#   resp["catalog_source"] = qoder_catalog.snapshot_source(req_realm)
# ---------------------------------------------------------------------------
def snapshot_source(realm):
    """该区域快照的**实际**来源（只读）。

    返回 SNAPSHOT_SOURCE_EXTERNAL（外部双区 JSON）或 SNAPSHOT_SOURCE_EMBEDDED
    （内嵌冻结副本 = 已降级，数据可能过期）。realm 非 "intl" 时按 "cn" 处理，
    与 models_for_realm() 的默认分支保持一致。
    """
    fn = _SNAPSHOT_FILENAMES.get(realm) or _SNAPSHOT_FILENAMES["cn"]
    return _SNAPSHOT_SOURCES.get(fn) or SNAPSHOT_SOURCE_EMBEDDED


def snapshot_report():
    """两区来源标注 + 降级原因 + 与内嵌副本的差异 key（只读诊断）。

    返回 {realm: {"source", "degrade_reason", "models", "embedded_diff_keys"}}。
    embedded_diff_keys 非空 = 外部 JSON 已与本文件内嵌副本漂移：此时若发生降级
    （外部文件丢失/损坏），这些模型的字段会整体换一套口径（峰谷价尤其明显）。
    注意：已降级（source=embedded-frozen）时该列表恒为空——比较对象此时就是内嵌
    副本自身；「是否已降级」请读 source / degrade_reason 两个字段，别看这个列表。
    """
    out = {}
    for realm, fn in _SNAPSHOT_FILENAMES.items():
        embedded = json.loads(_INTL_JSON if realm == "intl" else _CN_JSON)
        out[realm] = {
            "source": snapshot_source(realm),
            "degrade_reason": _SNAPSHOT_DEGRADE_REASONS.get(fn, ""),
            "models": len(models_for_realm(realm)),
            "embedded_diff_keys": sorted(
                _snapshot_key_diffs(embedded, models_for_realm(realm))),
        }
    return out


def _snapshot_key_diffs(embedded, loaded):
    """两侧「key 集合差 + 同 key 任一字段不同」的 key 集合（只读）。"""
    emb = {m.get("key"): m for m in embedded if isinstance(m, dict)}
    cur = {m.get("key"): m for m in loaded if isinstance(m, dict)}
    diffs = set(emb) ^ set(cur)
    for k in set(emb) & set(cur):
        a, b = emb[k], cur[k]
        if any(a.get(f) != b.get(f) for f in set(a) | set(b)):
            diffs.add(k)
    return diffs

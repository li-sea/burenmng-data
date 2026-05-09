"""
不认命 App - 快速测试版 API
功能：八字计算、寺庙匹配、法器推荐
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import random
import hashlib
import time
from datetime import datetime
import os
from bazi_lunar import BaziCalculator

# ============ 结果确定性缓存 ============
# 缓存结构: { input_hash: {"result": ..., "timestamp": ...} }
_result_cache = {}
# 缓存有效期：24 小时（秒），同一输入在有效期内返回相同结果
CACHE_TTL = 24 * 60 * 60

def _make_input_hash(year, month, day, hour, gender, prayer_focus, location):
    """生成确定性输入哈希（替代 Python hash()，后者跨进程不一致）"""
    key = f"{year}-{month}-{day}-{hour}-{gender}-{prayer_focus or ''}-{location or ''}"
    return hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]

def _get_cached_result(input_hash):
    """从缓存获取结果（如果未过期）"""
    if input_hash in _result_cache:
        entry = _result_cache[input_hash]
        if time.time() - entry["timestamp"] < CACHE_TTL:
            return entry["result"]
        else:
            del _result_cache[input_hash]
    return None

def _set_cached_result(input_hash, result):
    """缓存结果"""
    _result_cache[input_hash] = {"result": result, "timestamp": time.time()}

def _deterministic_seed(input_hash: str) -> int:
    """从哈希字符串生成确定性随机种子"""
    return int(hashlib.sha256(input_hash.encode('utf-8')).hexdigest()[:8], 16) % 100000000

# 初始化八字计算器
bazi_calc = BaziCalculator()

app = FastAPI(
    title="不认命 - 传统文化学习工具",
    description="八字知识与寺庙文化介绍 - 测试版",
    version="1.0.0"
)

# 允许 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（测试页面）
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import glob

WEB_TEST_DIR = os.path.join(os.path.dirname(__file__), "web_test")

@app.get("/test")
async def test_page():
    """测试页面"""
    return FileResponse(os.path.join(WEB_TEST_DIR, "index_new.html"))

@app.get("/test/pray")
async def pray_page():
    """祈福树测试页面"""
    return FileResponse(os.path.join(WEB_TEST_DIR, "pray_tree.html"))

# 加载寺庙数据
TEMPLES = []
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")

def load_temples():
    """加载所有寺庙数据"""
    global TEMPLES
    TEMPLES = []
    loaded_files = []
    
    # 加载所有 JSON 文件
    for filename in os.listdir(DATA_DIR):
        if filename.endswith(".json"):
            filepath = os.path.join(DATA_DIR, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        count = len(data)
                        TEMPLES.extend(data)
                        loaded_files.append(f"{filename}: {count}座")
                    elif isinstance(data, dict):
                        # 处理 {"meta": ..., "temples": [...]} 格式
                        temples_list = data.get('temples', [])
                        if temples_list:
                            count = len(temples_list)
                            TEMPLES.extend(temples_list)
                            loaded_files.append(f"{filename}: {count}座")
            except Exception as e:
                print(f"Error loading {filename}: {e}")
    
    # 去重（根据 name+province）
    seen = set()
    unique_temples = []
    for temple in TEMPLES:
        key = f"{temple.get('name', '')}_{temple.get('province', '')}"
        if key not in seen:
            seen.add(key)
            unique_temples.append(temple)
    
    TEMPLES = unique_temples
    
    print(f"Loaded files: {', '.join(loaded_files)}")
    print(f"Total temples (after dedup): {len(TEMPLES)}座")

load_temples()

# 请求模型
class BaziInput(BaseModel):
    year: int
    month: int
    day: int
    hour: int
    gender: str = "male"  # male or female

class MatchRequest(BaseModel):
    bazi: Optional[str] = None
    year: Optional[int] = None
    month: Optional[int] = None
    day: Optional[int] = None
    hour: Optional[int] = None
    gender: str = "male"
    prayer_focus: Optional[str] = None  # 祈福方向：事业、财运、姻缘、健康等
    location: Optional[str] = None  # 省份或城市

# 专业八字计算（使用 lunar-python）
def calculate_bazi(year: int, month: int, day: int, hour: int, minute: int = 0, gender: str = "male") -> Dict[str, Any]:
    """
    专业版八字计算（使用 lunar-python）
    """
    result = bazi_calc.calculate(year, month, day, hour, minute, gender)
    return result

# 五行属性（从 lunar-python 结果获取）
def get_wuxing(bazi_result: Dict) -> Dict[str, int]:
    """获取五行属性（从 lunar-python 结果）"""
    if "五行" in bazi_result and "分布" in bazi_result["五行"]:
        return bazi_result["五行"]["分布"]
    # 兼容旧版
    return {"木": 0, "火": 0, "土": 0, "金": 0, "水": 0}

# 寺庙分类标记
def categorize_temple(temple: Dict) -> str:
    """
    根据寺庙特征分类：famous(知名), local(地方/民间), folk(民间传说)
    """
    name = temple.get("name", "")
    features = temple.get("features", [])
    tags = temple.get("tags", [])
    rating = temple.get("rating", 0)
    
    # 知名寺庙判断：世界遗产、国家重点、历史名寺
    famous_keywords = ["世界遗产", "全国重点", "国家重点", "四大", "第一名刹", "祖庭", "皇家"]
    for kw in famous_keywords:
        if any(kw in str(f) for f in features) or any(kw in str(t) for t in tags):
            return "famous"
    
    # 5 星评级且评分人数多的也算知名
    if rating >= 5:
        return "famous"
    
    # 民间信仰、地方神庙算民间传说类
    folk_keywords = ["民间", "地方", "传说", "故事", "灵验", "本地"]
    for kw in folk_keywords:
        if any(kw in str(f) for f in features) or any(kw in str(t) for t in tags):
            return "folk"
    
    # 其他算地方寺庙
    return "local"

# 寺庙匹配算法（多样化版本）
def match_temples(bazi_result: Dict, prayer_focus: Optional[str] = None, location: Optional[str] = None, limit: int = 5, seed: Optional[int] = None) -> List[Dict]:
    """
    寺庙匹配算法 - 3个不同级别版本
    根据八字、祈福方向推荐3座不同级别的寺庙：
    1. 有名气的（知名古刹/全国闻名）
    2. 地方有名的（省级/市级知名）
    3. 民间传说（有故事的地方小庙）
    """
    import random as rand
    
    # 设置确定性随机种子（根据用户八字生成，保证同一用户结果稳定但不同用户不同）
    if seed is None:
        bazi_str = bazi_result.get("八字", {}).get("完整", "")
        # 使用 SHA256 哈希替代 Python hash()，确保跨进程一致性
        seed = _deterministic_seed(bazi_str)
    rand.seed(seed)
    
    wuxing = bazi_result["五行"]["分布"]
    weakest_wuxing = bazi_result["五行"]["最弱"]
    
    # 五行对应的寺庙类型
    wuxing_temple_types = {
        "木": ["道教", "佛教"],
        "火": ["道教", "文庙"],
        "土": ["佛教", "城隍庙"],
        "金": ["道教", "清真寺"],
        "水": ["佛教", "妈祖庙"]
    }
    
    # 祈福方向对应的寺庙
    prayer_temple_map = {
        "事业": ["道教", "关帝庙", "文庙"],
        "财运": ["财神庙", "道教", "关帝庙"],
        "姻缘": ["月老庙", "妈祖庙"],
        "健康": ["佛教", "道教", "药王庙"],
        "平安": ["佛教", "道教", "城隍庙", "妈祖庙"],
        "学业": ["文庙", "文昌阁"],
        "修行": ["佛教", "道教"]
    }
    
    # 分类收集寺庙
    famous_temples = []
    local_temples = []
    folk_temples = []
    
    for temple in TEMPLES:
        score = 0
        temple_type = temple.get("type", "")
        temple_subtype = temple.get("subtype", "")
        
        # 类型匹配
        preferred_types = wuxing_temple_types.get(weakest_wuxing, [])
        if temple_type in preferred_types or temple_subtype in preferred_types:
            score += 20
        
        # 祈福方向匹配
        if prayer_focus:
            preferred_for_prayer = prayer_temple_map.get(prayer_focus, [])
            if temple_type in preferred_for_prayer or temple_subtype in preferred_for_prayer:
                score += 30
        
        # 地理位置匹配（地方寺庙优先本地）
        if location:
            temple_province = temple.get("province", "")
            temple_city = temple.get("city", "")
            if location in temple_province or location in temple_city:
                score += 40
        
        temple_copy = temple.copy()
        temple_copy["match_score"] = score
        
        # 分类
        category = categorize_temple(temple)
        if category == "famous":
            famous_temples.append(temple_copy)
        elif category == "folk":
            folk_temples.append(temple_copy)
        else:
            local_temples.append(temple_copy)
    
    # 按分数排序
    famous_temples.sort(key=lambda x: x["match_score"], reverse=True)
    local_temples.sort(key=lambda x: x["match_score"], reverse=True)
    folk_temples.sort(key=lambda x: x["match_score"], reverse=True)
    
    # 新策略：固定 3 个不同级别
    result = []
    
    # 1. 有名气的：全国知名古刹（1座）
    if len(famous_temples) > 0:
        top_famous = famous_temples[:min(3, len(famous_temples))]
        selected = rand.sample(top_famous, 1)
        result.extend(selected)
    
    # 2. 地方有名的：省级/市级知名寺庙（1座）
    if len(local_temples) > 0:
        if location:
            local_matched = [t for t in local_temples if location in t.get("province", "") or location in t.get("city", "")]
            if len(local_matched) > 0:
                result.extend(rand.sample(local_matched[:5], 1))
            else:
                top_local = local_temples[:min(5, len(local_temples))]
                result.extend(rand.sample(top_local, 1))
        else:
            top_local = local_temples[:min(5, len(local_temples))]
            result.extend(rand.sample(top_local, 1))
    
    # 3. 民间传说：有故事的地方小庙（1座）
    if len(folk_temples) > 0:
        folk_with_stories = [t for t in folk_temples if t.get("folklore_stories") or t.get("description")]
        if len(folk_with_stories) > 0:
            top_folk = folk_with_stories[:min(5, len(folk_with_stories))]
            result.extend(rand.sample(top_folk, 1))
        else:
            top_folk = folk_temples[:min(5, len(folk_temples))]
            result.extend(rand.sample(top_folk, 1))
    
    # 添加个性化匹配原因
    reason_map = {"木": "生机与发展", "火": "热情与活力", "土": "稳定与包容", "金": "坚定与果断", "水": "智慧与变通"}
    prayer_focus_map = {
        "事业": {"keywords": ["事业", "智慧", "学业"], "wuxing": "木主事业"},
        "财运": {"keywords": ["财运", "生意", "财富"], "wuxing": "金主财运"},
        "姻缘": {"keywords": ["姻缘", "爱情", "婚姻"], "wuxing": "火主姻缘"},
        "健康": {"keywords": ["健康", "平安", "长寿"], "wuxing": "水主健康"},
        "平安": {"keywords": ["平安", "健康", "安"], "wuxing": "土主平安"},
        "学业": {"keywords": ["学业", "智慧", "文昌"], "wuxing": "木主学业"},
        "修行": {"keywords": ["修行", "智慧", "悟"], "wuxing": "水主修行"}
    }
    
    for temple in result:
        # 获取寺庙的祈福方向
        temple_prayers = temple.get("prayer_directions", [])
        temple_stories = temple.get("folklore_stories", [])
        temple_testimonials = temple.get("prayer_testimonials", [])
        temple_rating = temple.get("rating", 0)
        temple_type = temple.get("temple_type", "")
        temple_name = temple.get("name", "")
        
        # 第一步：五行基础
        reason_parts = []
        reason_parts.append(f"你的八字五行中{weakest_wuxing}较弱，传统五行文化认为{weakest_wuxing}主{reason_map.get(weakest_wuxing, '')}，需要补充{weakest_wuxing}的能量")
        
        # 第二步：匹配用户祈福方向与寺庙祈福方向
        if prayer_focus:
            focus_info = prayer_focus_map.get(prayer_focus, {})
            focus_keywords = focus_info.get("keywords", [prayer_focus])
            
            # 检查寺庙是否支持该祈福方向
            matched_directions = []
            for tp in temple_prayers:
                for kw in focus_keywords:
                    if kw in tp or tp in kw:
                        matched_directions.append(tp)
                        break
            
            if matched_directions:
                reason_parts.append(f"{temple_name}在民间信仰中以" + "、".join(matched_directions) + "著称，信众口碑灵验")
                # 找一个相关好评
                if temple_testimonials:
                    relevant = [t for t in temple_testimonials if any(kw in t.get("content", "") for kw in focus_keywords)]
                    if relevant:
                        best = max(relevant, key=lambda x: x.get("rating", 0))
                        reason_parts.append(f"有信众反馈：'{best['content']}'")
        
        # 第三步：结合文化故事
        if temple_stories:
            # 选一个最有代表性的故事
            story = temple_stories[0]
            story_type = story.get("type", "")
            story_title = story.get("title", "")
            if story_type == "英雄传说":
                reason_parts.append(f"传承历史文化：{story_title}，承载深厚文化底蕴")
            elif story_type == "显灵传说":
                reason_parts.append(f"民间广为流传{story_title}的传说，香火旺盛")
            elif story_type == "圣物传说":
                reason_parts.append(f"寺内{story_title}，被视为灵验之地")
            else:
                reason_parts.append(f"民间流传{story_title}的传说，具有深厚文化底蕴")
        
        # 第四步：寺庙类型定位
        if "关帝" in temple_type or "关帝" in temple_name:
            reason_parts.append("关帝信仰在民间被奉为武财神，护佑事业财运")
        elif "冼太" in temple_type or "冼太" in temple_name:
            reason_parts.append("冼太夫人是岭南圣母，护佑一方平安，德泽万民")
        elif "观音" in temple_type or "观音" in temple_name:
            reason_parts.append("观音菩萨大慈大悲，救苦救难，有求必应")
        elif "道观" in temple_type or "道教" in temple_type:
            reason_parts.append("道教文化讲究天人合一，顺应自然之道")
        elif "清真" in temple_type:
            reason_parts.append("伊斯兰文化崇尚和平与信仰的力量")
        
        # 第五步：评分背书
        reason_parts.append(f"文化评级{temple_rating}⭐")
        
        temple["match_reason"] = "。".join(reason_parts)
        
        # 添加寺庙类型标签（3个级别）
        category = categorize_temple(temple)
        if category == "famous":
            temple["temple_category"] = "知名古刹"
        elif category == "folk":
            temple["temple_category"] = "民间传说"
        else:
            temple["temple_category"] = "地方有名"
    
    return result[:limit]

# 法器推荐算法（佩戴建议版）
def recommend_faqi(bazi_result: Dict, temples: List[Dict]) -> List[Dict[str, str]]:
    """
    根据八字推荐佩戴物品，包含佩戴方式和部位
    """
    wuxing = bazi_result["五行"]["分布"]
    weakest_wuxing = bazi_result["五行"]["最弱"]
    
    # 五行对应的佩戴建议
    wuxing_faqi = {
        "木": [
            {
                "name": "绿幽灵水晶",
                "description": "对应木元素，象征生机与活力",
                "wear_method": "佩戴",
                "wear_position": "左手腕或颈部",
                "wear_detail": "建议佩戴在左手，靠近心脏位置，有助于吸收正能量",
                "reason": "你的五行中木较弱，绿幽灵水晶对应木元素，传统认为有助于补充木的能量"
            },
            {
                "name": "翡翠饰品",
                "description": "传统玉石，对应木元素",
                "wear_method": "佩戴",
                "wear_position": "左手或胸前",
                "wear_detail": "可做成手镯佩戴左手，或做成吊坠挂在胸前",
                "reason": "翡翠在传统文化中象征品德，木元素代表生长，有助于事业发展"
            },
            {
                "name": "木质佛珠",
                "description": "天然木材制成，对应木元素",
                "wear_method": "佩戴/手持",
                "wear_position": "手腕或手中",
                "wear_detail": "可戴在手腕上，也可在冥想时手持念诵",
                "reason": "木质材料直接对应木元素，传统认为有助于平静心境"
            }
        ],
        "火": [
            {
                "name": "红玛瑙",
                "description": "红色宝石，对应火元素",
                "wear_method": "佩戴",
                "wear_position": "左手腕或右手腕",
                "wear_detail": "建议佩戴在左手，红色有助于提升活力和热情",
                "reason": "你的五行中火较弱，红玛瑙对应火元素，传统认为有助于增强行动力"
            },
            {
                "name": "石榴石",
                "description": "红色系宝石，对应火元素",
                "wear_method": "佩戴",
                "wear_position": "左手腕",
                "wear_detail": "做成手链佩戴左手，有助于促进血液循环",
                "reason": "石榴石在传统文化中象征活力，火元素代表热情，有助于提升精神状态"
            },
            {
                "name": "红色中国结",
                "description": "传统吉祥物，对应火元素",
                "wear_method": "悬挂",
                "wear_position": "家门口或车内",
                "wear_detail": "悬挂在家门口、车内后视镜或办公桌旁",
                "reason": "红色中国结是传统吉祥物，火元素象征喜庆，有助于带来好运"
            }
        ],
        "土": [
            {
                "name": "黄水晶",
                "description": "黄色宝石，对应土元素",
                "wear_method": "佩戴/摆放",
                "wear_position": "右手腕或办公桌",
                "wear_detail": "可佩戴在右手，或做成摆件放在办公桌/财位",
                "reason": "你的五行中土较弱，黄水晶对应土元素，传统认为有助于稳定情绪和财运"
            },
            {
                "name": "玉石饰品",
                "description": "传统玉石，对应土元素",
                "wear_method": "佩戴",
                "wear_position": "左手腕或颈部",
                "wear_detail": "玉镯戴左手，玉佩挂在胸前靠近心脏",
                "reason": "玉石在中华文化中象征品德，土元素代表稳定，有助于身心平衡"
            },
            {
                "name": "陶瓷摆件",
                "description": "传统工艺品，对应土元素",
                "wear_method": "摆放",
                "wear_position": "家中西南方或办公位",
                "wear_detail": "放在家中西南方位或办公桌上，有助于稳定气场",
                "reason": "陶瓷由土烧制而成，直接对应土元素，传统认为有助于家庭和睦"
            }
        ],
        "金": [
            {
                "name": "白水晶",
                "description": "透明宝石，对应金元素",
                "wear_method": "佩戴/摆放",
                "wear_position": "右手腕或办公桌",
                "wear_detail": "可佩戴右手，或放在办公桌上有助于集中注意力",
                "reason": "你的五行中金较弱，白水晶对应金元素，传统认为有助于清晰思维"
            },
            {
                "name": "银饰",
                "description": "银质饰品，对应金元素",
                "wear_method": "佩戴",
                "wear_position": "左手腕或颈部",
                "wear_detail": "银手镯戴左手，银项链挂在颈部，有助于排毒",
                "reason": "银在民间传统中有特殊地位，金元素代表坚定，有助于增强决断力"
            },
            {
                "name": "金属风铃",
                "description": "金属制品，对应金元素",
                "wear_method": "悬挂",
                "wear_position": "家门口或窗边",
                "wear_detail": "悬挂在家门口或窗边，风吹过时有清脆声音",
                "reason": "金属风铃对应金元素，传统认为有助于驱散负能量"
            }
        ],
        "水": [
            {
                "name": "黑曜石",
                "description": "黑色宝石，对应水元素",
                "wear_method": "佩戴",
                "wear_position": "左手腕",
                "wear_detail": "建议佩戴左手，有助于排除负能量，增强直觉",
                "reason": "你的五行中水较弱，黑曜石对应水元素，传统认为有助于增强智慧"
            },
            {
                "name": "海蓝宝",
                "description": "蓝色宝石，对应水元素",
                "wear_method": "佩戴",
                "wear_position": "颈部或左手",
                "wear_detail": "做成吊坠挂在颈部，或手链戴左手，有助于沟通表达",
                "reason": "海蓝宝象征智慧与深邃，水元素代表流动，有助于人际关系"
            },
            {
                "name": "流水摆件",
                "description": "水景装饰，对应水元素",
                "wear_method": "摆放",
                "wear_position": "家中北方或办公桌",
                "wear_detail": "放在家中北方方位或办公桌上，水流方向朝向室内",
                "reason": "流水摆件直接对应水元素，传统认为有助于财运和智慧"
            }
        ]
    }
    
    # 返回对应五行的佩戴建议
    return wuxing_faqi.get(weakest_wuxing, wuxing_faqi["土"])

# API 路由
@app.get("/")
async def root():
    return {
        "message": "不认命 - 传统文化学习工具",
        "version": "1.0.0",
        "temples_loaded": len(TEMPLES),
        "docs": "/docs",
        "disclaimer": "本 API 仅供传统文化学习与研究使用"
    }

@app.get("/api/temples")
async def get_temples(
    province: Optional[str] = None,
    type: Optional[str] = None,
    limit: int = 10
):
    """获取寺庙列表"""
    result = TEMPLES
    
    if province:
        result = [t for t in result if province in t.get("province", "")]
    
    if type:
        result = [t for t in result if type in t.get("type", "") or type in t.get("subtype", "")]
    
    return result[:limit]

@app.post("/api/bazi")
async def calculate_bazi_endpoint(year: int, month: int, day: int, hour: int, minute: int = 0, gender: str = "male"):
    """计算八字（专业版）"""
    if not (1900 <= year <= 2100):
        raise HTTPException(status_code=400, detail="年份必须在 1900-2100 之间")
    if not (1 <= month <= 12):
        raise HTTPException(status_code=400, detail="月份必须在 1-12 之间")
    if not (1 <= day <= 31):
        raise HTTPException(status_code=400, detail="日期必须在 1-31 之间")
    if not (0 <= hour <= 23):
        raise HTTPException(status_code=400, detail="时辰必须在 0-23 之间")
    
    bazi_result = calculate_bazi(year, month, day, hour, minute, gender)
    
    return bazi_result

@app.post("/api/match")
async def match_temples_endpoint(request: MatchRequest):
    """寺庙匹配（专业版）- 支持结果确定性缓存"""
    # 计算八字
    year = request.year or 1990
    month = request.month or 1
    day = request.day or 1
    hour = request.hour or 12
    
    # 生成输入哈希，用于结果缓存
    input_hash = _make_input_hash(year, month, day, hour, request.gender, request.prayer_focus, request.location)
    
    # 检查缓存（24 小时内相同输入返回相同结果）
    cached = _get_cached_result(input_hash)
    if cached is not None:
        cached_copy = json.loads(json.dumps(cached))  # 深拷贝避免修改缓存
        cached_copy["_cache"] = "hit"  # 标记命中缓存
        return cached_copy
    
    bazi_result = calculate_bazi(year, month, day, hour, 0, request.gender)
    
    # 匹配寺庙
    temples = match_temples(
        bazi_result,
        prayer_focus=request.prayer_focus,
        location=request.location,
        limit=5
    )
    
    # 推荐法器
    faqi = recommend_faqi(bazi_result, temples)
    
    result = {
        "bazi": bazi_result["八字"],
        "wuxing": bazi_result["五行"]["分布"],
        "weakest": bazi_result["五行"]["最弱"],
        "xiyong": bazi_result["喜用神"],
        "minggua": bazi_result["命卦"],
        "temples": temples,
        "faqi": faqi,
        "advice": bazi_result["建议"],
        "_cache": "miss"
    }
    
    # 缓存结果（24 小时内相同输入返回相同结果）
    _set_cached_result(input_hash, result)
    
    return result

@app.get("/api/stats")
async def get_stats():
    """获取统计数据"""
    stats = {
        "total_temples": len(TEMPLES),
        "by_type": {},
        "by_province": {},
        "by_rating": {"5": 0, "4": 0, "3": 0}
    }
    
    for temple in TEMPLES:
        # 按类型统计
        t_type = temple.get("type", "其他")
        stats["by_type"][t_type] = stats["by_type"].get(t_type, 0) + 1
        
        # 按省份统计
        province = temple.get("province", "未知")
        stats["by_province"][province] = stats["by_province"].get(province, 0) + 1
        
        # 按评级统计
        rating = temple.get("rating", 0)
        if rating >= 5:
            stats["by_rating"]["5"] += 1
        elif rating >= 4:
            stats["by_rating"]["4"] += 1
        else:
            stats["by_rating"]["3"] += 1
    
    return stats

# ============ 祈福树 API ============
from pray_tree import pray_tree_manager

class CreateTreeRequest(BaseModel):
    user_id: str
    prayer_focus: str  # 事业/财运/姻缘/健康/平安/学业
    wish: str
    birth_date: Optional[str] = None  # 生日，用于星空图

class LikeTreeRequest(BaseModel):
    tree_id: str
    liker_id: str

@app.post("/api/pray-tree/create")
async def create_pray_tree(request: CreateTreeRequest):
    """种一棵祈福树"""
    try:
        tree = pray_tree_manager.create_tree(
            user_id=request.user_id,
            prayer_focus=request.prayer_focus,
            wish=request.wish,
            birth_date=request.birth_date
        )
        return {"status": "success", "tree": tree}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/pray-tree/like")
async def like_pray_tree(request: LikeTreeRequest):
    """给祈福树点赞"""
    try:
        tree = pray_tree_manager.like_tree(
            tree_id=request.tree_id,
            liker_id=request.liker_id
        )
        return {"status": "success", "tree": tree}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/pray-tree/hot")
async def get_hot_trees(limit: int = 20):
    """获取热门祈福树（点赞最多的）"""
    trees = pray_tree_manager.get_hot_trees(limit)
    return {"status": "success", "trees": trees}

@app.get("/api/pray-tree/random")
async def get_random_trees(limit: int = 20):
    """获取随机祈福树（用于祈福广场）"""
    trees = pray_tree_manager.get_random_trees(limit)
    return {"status": "success", "trees": trees}

@app.get("/api/pray-tree/user/{user_id}")
async def get_user_trees(user_id: str):
    """获取用户所有祈福树"""
    trees = pray_tree_manager.get_user_trees(user_id)
    return {"status": "success", "trees": trees}

@app.get("/api/pray-tree/{tree_id}")
async def get_pray_tree(tree_id: str):
    """获取祈福树信息"""
    try:
        tree = pray_tree_manager.get_tree(tree_id)
        return {"status": "success", "tree": tree}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.post("/api/pray-tree/{tree_id}/share")
async def share_pray_tree(tree_id: str):
    """分享祈福树"""
    try:
        share_info = pray_tree_manager.share_tree(tree_id)
        return {"status": "success", "share": share_info}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

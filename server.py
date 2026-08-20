"""拓界搜索 MVP：零第三方依赖的本地 Web 服务。"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8787
USER_AGENT = "Mozilla/5.0 (compatible; BeyondSearchMVP/0.1)"
DEFAULT_SEARXNG_URL = "http://127.0.0.1:8080"
SEARXNG_URL = os.getenv("SEARXNG_URL", DEFAULT_SEARXNG_URL).strip().rstrip("/")
SEARXNG_LANGUAGE = os.getenv("SEARXNG_LANGUAGE", "zh-CN").strip() or "zh-CN"
SEARXNG_ENGINES = os.getenv("SEARXNG_ENGINES", "baidu,google").strip() or "baidu,google"
SEARXNG_FALLBACK_ENGINES = os.getenv("SEARXNG_FALLBACK_ENGINES", "bing").strip() or "bing"
SEARXNG_CONCEPT_ENGINES = os.getenv("SEARXNG_CONCEPT_ENGINES", "yandex,zapmeta").strip() or "yandex,zapmeta"
SEARXNG_TIMEOUT = float(os.getenv("SEARXNG_TIMEOUT", "8.5"))
SEARCH_TOTAL_TIMEOUT = float(os.getenv("SEARCH_TOTAL_TIMEOUT", "9.0"))
SEARCH_CACHE_TTL = int(os.getenv("SEARCH_CACHE_TTL", "120"))
MAX_RESULT_PAGES = 3
SEARCH_CACHE: dict[tuple[str, int, int], tuple[float, dict[str, Any]]] = {}


@dataclass(frozen=True)
class SearchPlan:
    label: str
    query: str
    bridge: str
    reason: str
    distance: int
    # The user's untouched input. Low-divergence results must visibly contain
    # this anchor, which prevents an engine mistranslation from changing topic.
    anchor: str = ""


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str
    source: str
    display_url: str
    bridge: str
    reason: str
    distance: int


TOPIC_PROFILES: list[dict[str, Any]] = [
    {
        "keywords": ("人", "人类", "智人", "human", "humanity"),
        "original_curated": True,
        "original_bridge": "人类概念",
        "direct": [
            ("智人 人类演化 起源", "人类概念", "从物种演化与人类起源解释“人”。"),
            ("人体 解剖 生理 系统", "人类概念", "从身体结构和生命系统解释“人”。"),
            ("意识 认知 自我 人", "人类概念", "从意识、认知与自我经验解释“人”。"),
            ("人类学 语言 文化 社会", "人类概念", "从语言、文化和社会关系解释“人”。"),
        ],
        "adjacent": [
            ("智人 演化 人类起源", "人类演化", "把个体的人放入漫长的物种演化历史。"),
            ("文化人类学 亲属 社会", "文化人类学", "人不仅是生物个体，也生活在文化和关系网络中。"),
            ("意识 自我 心灵哲学", "意识与自我", "从主观体验和自我意识追问何以成为人。"),
        ],
        "cross": [
            ("尼安德特人 考古 基因", "古人类", "通过其他古人类重新认识现代人的独特与共同之处。"),
            ("动物文化 黑猩猩 工具", "动物文化", "比较其他动物的学习和工具行为，检验人的边界。"),
            ("人工智能 人格 道德地位", "人格边界", "用人工智能与人格问题反向审视“人”的定义。"),
            ("人口迁徙 语言地图", "人类迁徙", "从基因、语言和地理分布观察人类如何抵达世界各地。"),
        ],
    },
    {
        "keywords": ("电", "电力", "电气", "电能", "电流", "electric", "electricity"),
        "adjacent": [
            ("电的产生 电场 电流 电磁学", "电磁现象", "从日常用电回到电荷、电场和电流的物理基础。"),
            ("电网 储能 峰谷调度 可再生能源", "能源系统", "电无法大规模直接储存，因此会连接到电网平衡、储能与能源转型。"),
            ("电气安全 接地 漏电保护", "安全工程", "电的使用依赖接地、绝缘和保护装置来控制不可见的风险。"),
        ],
        "cross": [
            ("神经元 动作电位 生物电", "生物电", "神经和肌肉也依靠电位差传递信息，把电从机器延伸到生命。"),
            ("闪电 雷暴 大气放电", "大气电", "闪电是自然界大尺度放电现象，把家用电延伸到天气系统。"),
            ("电鳗 电器官 电感知 仿生", "生物仿生", "一些生物会发电或感知电场，为传感器和仿生工程提供另一条路径。"),
            ("有线电报 莫尔斯 电信史", "通信史", "电最早改变社会的方式之一是跨越距离传递信息。"),
        ],
    },
    {
        "keywords": ("代码", "编程", "软件", "程序", "开发", "算法", "性能", "debug", "coding"),
        "original_curated": True,
        "adjacent": [
            ("软件架构中的复杂度控制", "复杂度控制", "从局部优化扩展到系统结构与长期维护。"),
            ("认知负荷 工具设计", "认知负荷", "代码复杂度最终会转化为人的理解成本。"),
            ("排队论 系统吞吐 瓶颈", "瓶颈与吞吐", "程序和排队系统都关心等待、拥堵与资源分配。"),
        ],
        "cross": [
            ("极简主义 少即是多 设计", "删减与留白", "代码优化和极简主义都在研究怎样留下真正重要的东西。"),
            ("园林设计 留白 路径 边界", "空间与边界", "好的系统不只靠增加功能，也靠给变化留下空间。"),
            ("城市慢行系统 减少拥堵", "复杂网络", "代码、道路和组织流程都可以通过减少拥堵提升整体效率。"),
            ("钟表修复 机械诊断", "修复思维", "维护旧机器和维护旧代码，都需要先理解历史再决定改动边界。"),
        ],
    },
    {
        "keywords": ("学习", "考试", "复习", "课程", "教育", "知识", "论文", "study"),
        "adjacent": [
            ("间隔重复 学习科学", "记忆规律", "从学习内容扩展到记忆形成和遗忘规律。"),
            ("知识地图 概念关系", "知识结构", "学习不只是记住信息，也是在概念间建立路径。"),
            ("注意力恢复 认知科学", "注意力", "学习效率与大脑如何恢复注意力密切相关。"),
        ],
        "cross": [
            ("中世纪修道院 时间表", "节律与专注", "古老时间制度也在处理专注、休息和长期坚持。"),
            ("博物馆策展 如何组织知识", "策展思维", "策展与学习都需要选择、排列和解释材料。"),
            ("候鸟导航 地球磁场", "陌生认知系统", "观察其他生物如何定位，有助于跳出熟悉的学习模型。"),
            ("树木物候观察 日记", "慢观察", "长期观察能提供不同于短期记忆训练的知识积累方式。"),
        ],
    },
    {
        "keywords": ("工作", "效率", "团队", "管理", "项目", "会议", "职场", "productivity"),
        "adjacent": [
            ("组织行为 团队协作", "组织行为", "从工具效率扩展到团队互动和行为结构。"),
            ("排队论 工作流 瓶颈", "流程瓶颈", "工作流和服务系统都需要处理等待与拥堵。"),
            ("心理安全 团队", "心理安全", "团队效率不仅由流程决定，也由表达风险决定。"),
        ],
        "cross": [
            ("管弦乐团 排练 协作", "排练机制", "乐团展示了复杂团队如何依靠共同节奏协作。"),
            ("传统木船 建造 分工", "手工协作", "传统工程的协作方式提供了不同于现代项目管理的视角。"),
            ("蜂群 决策 机制", "分布式决策", "蜂群如何形成集体选择，是观察团队决策的另一条路径。"),
            ("城市交通 信号 调度", "调度与反馈", "团队任务和城市交通都依赖实时反馈与动态调度。"),
        ],
    },
]


GENERIC_PROFILE = {
    "adjacent": [
        ("{q} 历史 演变", "时间维度", "从当下问题扩展到它如何形成和变化。"),
        ("{q} 设计 原理", "设计原理", "换一个设计视角重新理解这个问题。"),
        ("{q} 社会影响", "社会联系", "观察这个问题如何进入更大的社会结构。"),
    ],
    "cross": [
        ("{q} 博物馆 馆藏 历史", "文化史", "从当下概念追溯它在博物馆、档案与历史中的变化。"),
        ("{q} 修复 维护 方法", "修复思维", "观察这个对象如何损坏、维护和恢复，建立一条实践性的跨域联系。"),
        ("{q} 自然界 仿生", "自然类比", "寻找自然界中与这个概念相似的结构或机制。"),
        ("{q} 地图 地理 分布", "空间表达", "把问题放到地图和地理分布中，观察空间差异。"),
        ("{q} 基础设施 社会系统", "系统联系", "观察这个概念如何嵌入基础设施和更大的社会系统。"),
    ],
}


GENERIC_DIRECT_FACETS = [
    ("{q}", "原始主题", "直接检索输入内容。"),
    ("{q} 是什么", "概念解释", "补充定义和入门解释。"),
    ("{q} 分类 类型", "分类体系", "从分类与类型扩展同一主题。"),
    ("{q} 原理 结构", "原理结构", "寻找主题背后的原理与结构。"),
    ("{q} 历史 演化", "历史演化", "沿着形成和演变过程理解主题。"),
    ("{q} 应用 影响", "应用影响", "查看主题的实际应用与影响。"),
]


CURATED_LIBRARY: dict[str, list[dict[str, str]]] = {
    "人类概念": [
        {"title": "人：作为物种、个体与文化主体", "url": "https://zh.wikipedia.org/wiki/%E4%BA%BA", "snippet": "从生物分类、社会关系、语言与文化等角度梳理“人”的基本含义。"},
        {"title": "智人：现代人类所属的物种", "url": "https://zh.wikipedia.org/wiki/%E6%99%BA%E4%BA%BA", "snippet": "了解智人的形态、演化、迁徙和与其他古人类的关系。"},
        {"title": "Smithsonian Human Origins：什么使我们成为人", "url": "https://humanorigins.si.edu/", "snippet": "以化石、遗传、工具和行为证据探索数百万年人类演化史。"},
        {"title": "Human Evolution Timeline：人类演化时间线", "url": "https://humanorigins.si.edu/evidence/human-evolution-interactive-timeline", "snippet": "交互查看不同古人类、气候变化与重要行为证据出现的时间。"},
        {"title": "Natural History Museum：人类演化", "url": "https://www.nhm.ac.uk/discover/human-evolution.html", "snippet": "从化石和基因证据了解现代人如何形成并走向全球。"},
        {"title": "The Leakey Foundation：人类起源研究", "url": "https://leakeyfoundation.org/", "snippet": "聚合古人类学、灵长类学与人类演化的一线研究和科普。"},
        {"title": "Max Planck Institute：演化人类学", "url": "https://www.eva.mpg.de/", "snippet": "研究人类基因、语言、文化、行为和灵长类近亲。"},
        {"title": "NHGRI：人类基因组计划", "url": "https://www.genome.gov/human-genome-project", "snippet": "了解人类基因组测序如何改变医学、生物学和对人类共同性的认识。"},
        {"title": "OpenStax Anatomy & Physiology：人体解剖与生理", "url": "https://openstax.org/details/books/anatomy-and-physiology-2e", "snippet": "免费的系统教材，覆盖细胞、组织、器官与人体各大系统。"},
        {"title": "Visible Human Project：数字人体资料", "url": "https://www.nlm.nih.gov/research/visible/visible_human.html", "snippet": "美国国家医学图书馆建立的高分辨率数字人体解剖数据。"},
        {"title": "Innerbody：交互式人体系统", "url": "https://www.innerbody.com/htm/body.html", "snippet": "通过交互模型认识骨骼、肌肉、神经、循环和其他人体系统。"},
        {"title": "Khan Academy：人体生物学", "url": "https://www.khanacademy.org/science/biology/human-biology", "snippet": "用课程和图解学习人体循环、呼吸、神经、免疫等生命过程。"},
        {"title": "Stanford Encyclopedia：人的本性", "url": "https://plato.stanford.edu/entries/human-nature/", "snippet": "从哲学、生物学和社会科学讨论是否存在共同的“人的本性”。"},
        {"title": "Stanford Encyclopedia：意识", "url": "https://plato.stanford.edu/entries/consciousness/", "snippet": "梳理意识、主观体验及其与大脑和身体关系的主要理论。"},
        {"title": "Stanford Encyclopedia：人格同一性", "url": "https://plato.stanford.edu/entries/identity-personal/", "snippet": "探讨一个人跨越时间仍是同一个人的条件。"},
        {"title": "Internet Encyclopedia of Philosophy：心灵哲学", "url": "https://iep.utm.edu/category/m-and-e/philosophy-of-mind/", "snippet": "介绍心身关系、意识、自我、知觉与人工心智等问题。"},
        {"title": "SAPIENS：面向公众的人类学杂志", "url": "https://www.sapiens.org/", "snippet": "以考古、文化、生物和语言人类学理解人类经验的多样性。"},
        {"title": "Perspectives：开放文化人类学教材", "url": "https://perspectives.americananthro.org/", "snippet": "从亲属、语言、宗教、经济、政治和全球化理解不同社会中的人。"},
        {"title": "WALS：世界语言结构地图集", "url": "https://wals.info/", "snippet": "用地图和数据库比较全球语言的语音、语法与结构特征。"},
        {"title": "Ethnologue：世界语言资料", "url": "https://www.ethnologue.com/", "snippet": "观察人类语言的分布、使用人口、活力与谱系关系。"},
        {"title": "Our World in Data：人口与人口结构", "url": "https://ourworldindata.org/population-growth", "snippet": "用长期数据理解世界人口增长、年龄结构和地区差异。"},
        {"title": "United Nations：世界人口展望", "url": "https://www.un.org/development/desa/pd/content/world-population-prospects-2024", "snippet": "联合国关于全球人口规模、出生、死亡和迁徙的权威估计。"},
        {"title": "Human Development Reports：人的发展", "url": "https://hdr.undp.org/", "snippet": "从健康、教育、收入与能力角度衡量人的发展，而不只看经济增长。"},
        {"title": "UNESCO：文化多样性", "url": "https://www.unesco.org/en/cultural-diversity", "snippet": "理解文化表达、身份、交流与人类共同遗产之间的关系。"},
        {"title": "National Geographic：全球人类迁徙", "url": "https://education.nationalgeographic.org/resource/global-human-journey/", "snippet": "从考古和遗传证据观察现代人走出非洲并遍布全球的过程。"},
        {"title": "Human Protein Atlas：人体蛋白质图谱", "url": "https://www.proteinatlas.org/humanproteome", "snippet": "以开放数据观察蛋白质在人体组织、细胞和器官中的分布。"},
        {"title": "Allen Human Brain Atlas：人脑图谱", "url": "https://human.brain-map.org/", "snippet": "把人脑的解剖结构、基因表达与神经科学数据放到同一个交互图谱中。"},
        {"title": "WHO：人的健康与生命历程", "url": "https://www.who.int/health-topics", "snippet": "从生命历程、疾病、环境和公共卫生理解影响人类健康的因素。"},
        {"title": "World Values Survey：人类价值观调查", "url": "https://www.worldvaluessurvey.org/", "snippet": "通过跨国长期调查比较人的价值观、信任、家庭、宗教与社会态度。"},
        {"title": "Harvard Human Flourishing Program：人的幸福与发展", "url": "https://hfh.fas.harvard.edu/", "snippet": "跨学科研究健康、幸福、意义、品格和社会关系如何共同构成人的繁荣。"},
    ],
    "原始问题": [
        {"title": "MDN Web Docs：Web 开发技术文档", "url": "https://developer.mozilla.org/zh-CN/", "snippet": "面向 Web 开发者的开放技术文档，包含性能、JavaScript、CSS、网络与浏览器 API。"},
        {"title": "web.dev：构建快速、易用的现代网站", "url": "https://web.dev/", "snippet": "提供 Web 性能、可访问性、用户体验和工程实践指南。"},
        {"title": "Martin Fowler：软件设计与重构文章", "url": "https://martinfowler.com/", "snippet": "关于软件架构、重构、持续交付和长期维护的经典文章集合。"},
        {"title": "Computer Science from the Bottom Up", "url": "https://bottomupcs.com/", "snippet": "从计算机底层原理出发理解程序、操作系统、编译与性能。"},
    ],
    "复杂度控制": [
        {"title": "Software Engineering at Google：工程规模与复杂度", "url": "https://abseil.io/resources/swe-book", "snippet": "讨论软件如何跨越时间与规模持续演化，以及团队如何控制工程复杂度。"},
        {"title": "Refactoring.Guru：重构与设计模式", "url": "https://refactoring.guru/", "snippet": "通过图解学习重构、设计模式和代码结构改善方法。"},
        {"title": "The Architecture of Open Source Applications", "url": "https://aosabook.org/", "snippet": "由开源项目作者解释真实软件系统背后的架构选择。"},
    ],
    "认知负荷": [
        {"title": "Nielsen Norman Group：认知负荷与界面设计", "url": "https://www.nngroup.com/articles/minimize-cognitive-load/", "snippet": "解释复杂界面如何增加人的理解负担，以及如何减少不必要的认知成本。"},
        {"title": "The Design of Everyday Things：日常事物的设计", "url": "https://www.nngroup.com/books/design-everyday-things-revised/", "snippet": "从可理解性、反馈和错误预防重新认识产品与工具设计。"},
        {"title": "Laws of UX：心理学规律与交互设计", "url": "https://lawsofux.com/", "snippet": "用简洁案例说明心理学规律如何影响用户理解复杂系统。"},
    ],
    "瓶颈与吞吐": [
        {"title": "Seeing Theory：可视化概率与统计", "url": "https://seeing-theory.brown.edu/", "snippet": "通过交互图形理解概率、随机过程与统计推断。"},
        {"title": "Queueing Theory：排队系统的直观介绍", "url": "https://people.revoledu.com/kardi/tutorial/Queuing/", "snippet": "从到达率、服务率和等待时间理解系统瓶颈与吞吐。"},
        {"title": "High Scalability：大型系统案例", "url": "http://highscalability.com/", "snippet": "通过真实互联网系统案例观察规模、瓶颈、缓存与可靠性。"},
    ],
    "删减与留白": [
        {"title": "Minimalissimo：极简主义设计档案", "url": "https://minimalissimo.com/", "snippet": "从建筑、产品、平面与空间设计观察删减、克制和必要性。"},
        {"title": "LessWrong：关于简洁解释与思维模型的文章", "url": "https://www.lesswrong.com/", "snippet": "探索理性思考、模型简化和复杂问题中的认知偏差。"},
        {"title": "The Minimalists：关于减少与选择的文章", "url": "https://www.theminimalists.com/", "snippet": "从生活哲学角度讨论减少冗余、明确优先级与保留重要事物。"},
    ],
    "空间与边界": [
        {"title": "Japanese Gardening：日本庭园的空间原则", "url": "https://www.japanesegardening.org/", "snippet": "从路径、借景、留白和边界理解空间如何引导人的注意力。"},
        {"title": "ArchDaily：庭院与景观设计案例", "url": "https://www.archdaily.com/search/projects/categories/landscape-architecture", "snippet": "浏览世界各地景观项目，观察空间、动线与环境之间的关系。"},
        {"title": "Landscape Performance Series", "url": "https://www.landscapeperformance.org/", "snippet": "用案例和数据研究景观设计如何影响环境、社会和空间使用。"},
    ],
    "复杂网络": [
        {"title": "Streetmix：交互式街道设计工具", "url": "https://streetmix.net/", "snippet": "重新组合道路、步行、骑行和公共交通空间，直观观察复杂系统的取舍。"},
        {"title": "MIT Senseable City Lab：城市数据与流动", "url": "https://senseable.mit.edu/", "snippet": "以数据和实验探索城市交通、公共空间与技术系统。"},
        {"title": "NACTO：城市街道设计指南", "url": "https://nacto.org/publication/urban-street-design-guide/", "snippet": "研究城市如何通过重新分配路径与空间改善安全和流动。"},
    ],
    "修复思维": [
        {"title": "iFixit：开放维修指南", "url": "https://www.ifixit.com/Guide", "snippet": "数千份设备拆解和维修指南，展示诊断、拆分与恢复功能的过程。"},
        {"title": "The Repair Association：维修权与可修复设计", "url": "https://www.repair.org/", "snippet": "从产品、政策和可持续性角度讨论为什么系统应该允许被理解和修复。"},
        {"title": "Horology：钟表结构与修复资料", "url": "https://www.horology.org/", "snippet": "了解机械钟表、计时结构和精密维修背后的知识。"},
    ],
    "数字档案": [
        {"title": "Internet Archive：互联网与公共文化的数字档案馆", "url": "https://archive.org/", "snippet": "浏览网页、书籍、录音、电影与软件等公共数字馆藏。"},
        {"title": "The Public Domain Review：公共领域中的奇异历史", "url": "https://publicdomainreview.org/", "snippet": "以专题文章和图像收藏重新发现艺术、科学与思想史中的冷门材料。"},
        {"title": "Smithsonian Open Access：开放博物馆藏品", "url": "https://www.si.edu/openaccess", "snippet": "访问史密森尼开放的博物馆和研究藏品数据。"},
    ],
    "时间维度": [
        {"title": "Google Arts & Culture：历史与文化专题", "url": "https://artsandculture.google.com/", "snippet": "通过档案、艺术品和时间线理解事物如何形成和演变。"},
        {"title": "Our World in Data：长期数据变化", "url": "https://ourworldindata.org/", "snippet": "使用公开数据观察技术、社会与环境问题的长期变化。"},
    ],
    "设计原理": [
        {"title": "Design Principles FTW", "url": "https://www.designprinciplesftw.com/", "snippet": "收集不同组织与设计师使用的产品设计原则。"},
        {"title": "Designing for the Web：开放设计书", "url": "https://designingfortheweb.co.uk/", "snippet": "从排版、网格与视觉层级理解 Web 设计基础。"},
    ],
    "社会联系": [
        {"title": "Pew Research Center：互联网与社会研究", "url": "https://www.pewresearch.org/internet/", "snippet": "研究技术、媒体与社会行为之间的联系。"},
        {"title": "Data & Society：技术的社会影响", "url": "https://datasociety.net/", "snippet": "探索自动化、平台、数据和人工智能如何进入社会结构。"},
    ],
    "慢观察": [
        {"title": "iNaturalist：记录和辨认自然观察", "url": "https://www.inaturalist.org/", "snippet": "记录身边生物并参与全球自然观察社区。"},
        {"title": "Nature's Notebook：物候观察计划", "url": "https://www.usanpn.org/nn", "snippet": "通过长期记录植物和动物的季节变化理解自然节律。"},
    ],
    "空间表达": [
        {"title": "David Rumsey Map Collection：历史地图档案", "url": "https://www.davidrumsey.com/", "snippet": "浏览数万幅历史地图，观察世界如何被测量、组织和表达。"},
        {"title": "The Map Room：地图与制图文化", "url": "https://www.maproomblog.com/", "snippet": "收集地图、制图技术和空间表达相关的文章与项目。"},
    ],
    "隐形基础设施": [
        {"title": "Submarine Cable Map：全球海底电缆地图", "url": "https://www.submarinecablemap.com/", "snippet": "可视化连接全球互联网的海底光缆与登陆点。"},
        {"title": "The Internet's Undersea World", "url": "https://99percentinvisible.org/episode/episode-70-the-great-undersea-cable/", "snippet": "从设计与历史角度理解隐藏在海底的通信基础设施。"},
    ],
    "生物电": [
        {"title": "动作电位：神经元如何用电传递信号", "url": "https://zh.wikipedia.org/wiki/%E5%8A%A8%E4%BD%9C%E7%94%B5%E4%BD%8D", "snippet": "动作电位是细胞膜电位快速变化的过程，是神经和肌肉传递信息的基础。"},
        {"title": "神经元：生命系统中的电信号", "url": "https://zh.wikipedia.org/wiki/%E7%A5%9E%E7%BB%8F%E5%85%83", "snippet": "神经元通过电信号和化学信号接收、处理并传递信息。"},
        {"title": "Khan Academy：细胞膜电位的形成", "url": "https://www.khanacademy.org/science/biology/human-biology/neuron-nervous-system/a/the-membrane-potential", "snippet": "从离子浓度、选择性通透和电化学梯度理解神经细胞为何带电。"},
        {"title": "OpenStax：神经组织与电信号", "url": "https://openstax.org/books/anatomy-and-physiology-2e/pages/12-2-nervous-tissue", "snippet": "开放教材系统介绍神经元、静息电位、动作电位和突触传递。"},
        {"title": "Neuroscientifically Challenged：动作电位图解", "url": "https://neuroscientificallychallenged.com/posts/action-potential", "snippet": "通过图解观察钠钾离子流动如何构成神经元的电脉冲。"},
        {"title": "BrainFacts：神经元如何交流", "url": "https://www.brainfacts.org/brain-anatomy-and-function/cells-and-circuits/2012/how-neurons-communicate", "snippet": "从电信号与化学突触两部分解释大脑中的信息传递。"},
    ],
    "大气电": [
        {"title": "闪电：发生在大气中的大尺度放电", "url": "https://zh.wikipedia.org/wiki/%E9%97%AA%E7%94%B5", "snippet": "闪电连接了电荷分离、大气运动、放电通道与雷暴天气。"},
        {"title": "球状闪电：罕见的大气电现象", "url": "https://zh.wikipedia.org/wiki/%E7%90%83%E7%8A%B6%E9%97%AA%E7%94%B5", "snippet": "球状闪电是一类罕见且仍有争议的大气发光与放电现象。"},
        {"title": "NOAA SciJinks：闪电是怎样形成的", "url": "https://scijinks.gov/lightning/", "snippet": "美国海洋和大气管理局用图文解释雷暴云中的电荷分离与放电。"},
        {"title": "UCAR：雷暴、闪电与大气科学", "url": "https://scied.ucar.edu/learning-zone/storms/lightning", "snippet": "从风暴内部的冰晶碰撞、电荷累积和先导通道理解闪电。"},
        {"title": "英国气象局：关于闪电的事实", "url": "https://www.metoffice.gov.uk/weather/learn-about/weather/types-of-weather/thunder-and-lightning/facts-about-lightning", "snippet": "介绍闪电类型、温度、传播过程及雷暴天气安全知识。"},
        {"title": "NASA Earthdata：从太空观测闪电", "url": "https://www.earthdata.nasa.gov/topics/atmosphere/lightning", "snippet": "利用卫星数据研究全球闪电分布以及它与气候和极端天气的联系。"},
    ],
    "生物仿生": [
        {"title": "电鳗：会发电的生物系统", "url": "https://zh.wikipedia.org/wiki/%E9%9B%BB%E9%B0%BB%E7%9B%AE", "snippet": "电鳗利用特化电器官产生电压，也能借助电场感知周围环境。"},
        {"title": "电感受：动物如何感知微弱电场", "url": "https://zh.wikipedia.org/wiki/%E7%94%B5%E6%84%9F%E5%8F%97", "snippet": "电感受把电与生物传感联系起来，并启发水下探测和仿生传感器。"},
        {"title": "Britannica：电鳗如何产生电流", "url": "https://www.britannica.com/animal/electric-eel", "snippet": "介绍电鳗的电器官、放电方式、捕食行为与环境感知能力。"},
        {"title": "Smithsonian Ocean：会发电和感知电的鱼", "url": "https://ocean.si.edu/ocean-life/fish/electric-fishes", "snippet": "观察不同鱼类如何利用电信号导航、交流、防御和捕食。"},
        {"title": "National Geographic：电鳗并不是真正的鳗鱼", "url": "https://www.nationalgeographic.com/animals/fish/facts/electric-eel", "snippet": "从演化和生态角度认识电鳗的高压放电与弱电感知系统。"},
        {"title": "AskNature：从生物电系统寻找工程灵感", "url": "https://asknature.org/strategy/electric-organs-generate-high-voltage/", "snippet": "把电器官的细胞串联结构转化为柔性电源和仿生能源设计线索。"},
    ],
    "通信史": [
        {"title": "电报：电如何第一次压缩通信距离", "url": "https://zh.wikipedia.org/wiki/%E7%94%B5%E6%8A%A5", "snippet": "有线电报利用电脉冲跨越长距离传递文字，改变了新闻、铁路与社会协作。"},
        {"title": "摩尔斯电码：把文字编码为电信号", "url": "https://zh.wikipedia.org/wiki/%E6%91%A9%E5%B0%94%E6%96%AF%E7%94%B5%E7%A0%81", "snippet": "摩尔斯电码将字符转换成长短信号，是早期电报通信的关键编码方法。"},
        {"title": "Smithsonian：电报与电话藏品", "url": "https://americanhistory.si.edu/collections/object-groups/telegraph-and-telephone", "snippet": "通过实物档案观察电报机、线路和电话如何重塑远距离通信。"},
        {"title": "美国国会图书馆：Samuel Morse 档案", "url": "https://www.loc.gov/collections/samuel-f-b-morse-papers/about-this-collection/", "snippet": "从书信、草图和记录了解摩尔斯及早期电报网络的发展。"},
        {"title": "Britannica：电报的发明与演变", "url": "https://www.britannica.com/technology/telegraph", "snippet": "梳理光学电报、电磁电报、海底线路和全球通信网络的形成。"},
        {"title": "Science Museum：改变世界的电报", "url": "https://www.sciencemuseum.org.uk/objects-and-stories/telecommunications/telegraphy-victorian-internet", "snippet": "从维多利亚时代的互联网理解电报如何改变商业、新闻和私人生活。"},
    ],
}


DEFAULT_CURATED = [item for group in CURATED_LIBRARY.values() for item in group]


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", value).strip()


def host_label(url: str) -> tuple[str, str]:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.removeprefix("www.") or "网页"
    display = host + (parsed.path.rstrip("/") if parsed.path not in ("", "/") else "")
    return host, display.replace("/", " › ")


def profile_matches(profile: dict[str, Any], query: str) -> bool:
    lowered = query.lower().strip()
    return any(lowered == keyword if len(keyword) == 1 else keyword in lowered for keyword in profile["keywords"])


def profile_for(query: str) -> dict[str, Any]:
    lowered = query.lower()
    for profile in TOPIC_PROFILES:
        if profile_matches(profile, lowered):
            return profile
    return {
        "adjacent": [(q.format(q=query), bridge, reason) for q, bridge, reason in GENERIC_PROFILE["adjacent"]],
        "cross": [(q.format(q=query), bridge, reason) for q, bridge, reason in GENERIC_PROFILE["cross"]],
    }


def has_known_profile(query: str) -> bool:
    return any(profile_matches(profile, query) for profile in TOPIC_PROFILES)


def has_original_curated_results(query: str) -> bool:
    return any(profile.get("original_curated", False) and profile_matches(profile, query) for profile in TOPIC_PROFILES)


def build_search_plans(query: str, divergence: int) -> list[SearchPlan]:
    profile = profile_for(query)
    seed = int(hashlib.sha256(f"{query}:{divergence}".encode("utf-8")).hexdigest()[:12], 16)

    def rotate(items: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
        if not items:
            return []
        offset = seed % len(items)
        return items[offset:] + items[:offset]

    adjacent = rotate(list(profile["adjacent"]))
    cross = rotate(list(profile["cross"]))
    # Direct facets are universal rather than maintained keyword-by-keyword.
    # They give broad, short and previously unseen queries enough recall while
    # staying anchored to exactly what the user entered.
    direct = [
        (facet_query.format(q=query), bridge, reason)
        for facet_query, bridge, reason in GENERIC_DIRECT_FACETS
    ]
    original_bridge = profile.get("original_bridge", "原始问题")
    plans: list[SearchPlan] = []

    if divergence <= 10:
        if divergence == 0:
            mix = [(direct[0], 0)]
        else:
            # Two broad facets normally yield 20–30 candidates. Keeping this
            # bounded is important: request bursts cause public adapters to
            # suspend themselves and turn a valid query into a zero-result UI.
            mix = [
                (direct[1], divergence),
                (direct[2], divergence),
            ]
    elif divergence <= 25:
        mix = [
            (direct[0], 0),
            (direct[1], min(divergence, 12)),
            (direct[2], min(divergence, 16)),
            (adjacent[0], min(divergence, 22)),
            (adjacent[1], divergence),
        ]
    elif divergence <= 55:
        mix = [(direct[0], 0), (adjacent[0], 35), (adjacent[1], 43), (cross[0], 58)]
    elif divergence <= 80:
        mix = [(direct[0], 0), (adjacent[0], 42), (cross[0], 62), (cross[1], 72), (cross[2], 80)]
    else:
        mix = [(direct[0], 0), (cross[0], 72), (cross[1], 82), (cross[2], 90), (cross[3 % len(cross)], 96)]

    for (search_query, bridge, reason), distance in mix:
        label = "主题分面" if distance <= 10 else "跨域方向"
        plans.append(SearchPlan(label, search_query, bridge, reason, distance, query))
    return plans


def searxng_search(plan: SearchPlan, limit: int = 3, page: int = 1) -> list[SearchResult]:
    """Run one planned query against a SearXNG JSON endpoint."""
    has_chinese = bool(re.search(r"[\u4e00-\u9fff]", plan.query))
    # On the current SearXNG deployment, Bing mistranslates some one-character
    # Chinese queries and the Baidu/Google adapters are CAPTCHA-blocked. Do not
    # spend latency on sources whose output would be discarded by the anchor
    # filter; use them normally for non-Chinese searches.
    if has_chinese:
        # Keep each adapter independent. A combined SearXNG engine group is
        # all-or-nothing when one member times out, which made valid Chinese
        # queries appear as zero-result searches.
        engine_groups = [
            engine.strip()
            for engine in SEARXNG_CONCEPT_ENGINES.split(",")
            if engine.strip()
        ] or [SEARXNG_CONCEPT_ENGINES]
    else:
        engine_groups = list(dict.fromkeys([
            SEARXNG_ENGINES,
            SEARXNG_FALLBACK_ENGINES,
            SEARXNG_CONCEPT_ENGINES,
        ]))
    items: list[Any] = []
    last_error: Exception | None = None

    def fetch_items(engines: str, result_page: int) -> list[Any]:
        params = urllib.parse.urlencode(
            {
                "q": plan.query,
                "format": "json",
                "language": SEARXNG_LANGUAGE,
                "safesearch": 1,
                "categories": "general",
                "engines": engines,
                "pageno": result_page,
            }
        )
        request = urllib.request.Request(
            f"{SEARXNG_URL}/search?{params}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=SEARXNG_TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
        response_items = payload.get("results")
        if not isinstance(response_items, list):
            raise ValueError("SearXNG response does not contain a results list")
        return response_items

    # Primary and backup engines race each other. This avoids waiting for a
    # CAPTCHA-blocked source before trying the healthy one.
    # At divergence 0 there is only one plan, so later upstream pages provide
    # normal search pagination. Once semantic facets are active, each facet
    # contributes one page: requesting three pages for every facet would burst
    # the upstream service and trigger its temporary rate limiter.
    # Each semantic facet queries one upstream page. Multiple engines are
    # isolated above, so one can fail without erasing another engine's result.
    result_pages = [page]
    requests = [(engines, result_page) for engines in engine_groups for result_page in result_pages]
    executor = ThreadPoolExecutor(max_workers=len(requests), thread_name_prefix="searxng-engine")
    futures = [executor.submit(fetch_items, engines, result_page) for engines, result_page in requests]
    try:
        for future in as_completed(futures, timeout=SEARXNG_TIMEOUT + 0.25):
            try:
                response_items = future.result()
                if response_items:
                    items.extend(response_items)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError) as error:
                last_error = error
    except TimeoutError as error:
        last_error = error
    finally:
        for future in futures:
            if not future.done():
                future.cancel()
        executor.shutdown(wait=False, cancel_futures=True)

    if not items and last_error is not None:
        raise last_error

    ranked: list[tuple[float, int, SearchResult]] = []
    def compact(value: str) -> str:
        return "".join(re.findall(r"[a-zA-Z0-9+#.]+|[\u4e00-\u9fff]+", value.lower()))

    query_normalized = compact(plan.query)
    anchor_normalized = compact(plan.anchor)
    # Whitespace-delimited Chinese concepts are kept as independent terms;
    # unlike the old expression this intentionally supports one-character
    # topics such as 鸟、树、水 and 电.
    query_terms = [
        term.lower()
        for term in re.findall(r"[a-zA-Z0-9+#.]+|[\u4e00-\u9fff]+", plan.query)
    ]

    for item in items:
        if not isinstance(item, dict):
            continue
        title = clean_text(str(item.get("title") or ""))
        url = clean_text(str(item.get("url") or ""))
        snippet = clean_text(str(item.get("content") or item.get("snippet") or ""))
        if not title or not url or urllib.parse.urlparse(url).scheme not in {"http", "https"}:
            continue
        source, display_url = host_label(url)
        result = SearchResult(title, url, snippet, source, display_url, plan.bridge, plan.reason, plan.distance)
        title_lower = title.lower()
        snippet_lower = snippet.lower()
        title_compact = compact(title_lower)
        snippet_compact = compact(snippet_lower)
        combined_compact = title_compact + snippet_compact
        score = float(item.get("score") or 0)
        relevance_hits = 0
        if query_normalized and query_normalized in title_compact:
            score += 30
            relevance_hits += 3
        elif query_normalized and query_normalized in snippet_compact:
            score += 10
            relevance_hits += 2
        title_term_hits = sum(1 for term in query_terms if term in title_lower)
        snippet_term_hits = sum(1 for term in query_terms if term in snippet_lower)
        matched_terms = {term for term in query_terms if term in title_lower or term in snippet_lower}
        score += 7 * title_term_hits + 2 * snippet_term_hits
        relevance_hits += title_term_hits + snippet_term_hits

        anchor_match = bool(anchor_normalized and anchor_normalized in combined_compact)
        if anchor_match:
            score += 24 if anchor_normalized in title_compact else 9

        # Search engines occasionally return CAPTCHA artefacts or unrelated
        # foreign pages. A detour is valid only when the page visibly shares
        # at least one term or Chinese bigram with its planned query.
        if relevance_hits == 0:
            continue
        # At low divergence the original topic is a hard contract. At larger
        # distances a page must instead substantiate at least two concepts in
        # the planned bridge, so a random one-word collision is not enough.
        if plan.distance <= 25 and anchor_normalized and not anchor_match:
            continue
        if plan.distance > 25 and len(query_terms) > 1 and len(matched_terms) < 2:
            continue
        ranked.append((score, len(ranked), result))

    ranked.sort(key=lambda entry: (-entry[0], entry[1]))
    results: list[SearchResult] = []
    source_counts: dict[str, int] = {}
    per_source_limit = limit
    for _score, _position, result in ranked:
        if source_counts.get(result.source, 0) >= per_source_limit:
            continue
        results.append(result)
        source_counts[result.source] = source_counts.get(result.source, 0) + 1
        if len(results) >= limit:
            break
    return results


def fallback_results(plans: list[SearchPlan], limit: int, include_original: bool = True, page: int = 1) -> list[SearchResult]:
    results: list[SearchResult] = []
    if not plans:
        return results

    # Round-robin by plan so the requested divergence controls both the
    # explanation and the returned websites, even when live search is offline.
    plans = [plan for plan in plans if include_original or plan.bridge != "原始问题"]
    cursors: dict[str, int] = {}
    target_count = page * limit
    generated: list[SearchResult] = []
    while len(generated) < target_count:
        added = False
        for plan in plans:
            candidates = CURATED_LIBRARY.get(plan.bridge, [])
            cursor = cursors.get(plan.bridge, 0)
            if cursor >= len(candidates):
                continue
            item = candidates[cursor]
            cursors[plan.bridge] = cursor + 1
            source, display_url = host_label(item["url"])
            generated.append(SearchResult(item["title"], item["url"], item["snippet"], source, display_url, plan.bridge, plan.reason, plan.distance))
            added = True
            if len(generated) >= target_count:
                break
        if not added:
            break
    start = (page - 1) * limit
    return generated[start:start + limit]


def deduplicate(results: list[SearchResult], limit: int) -> list[SearchResult]:
    seen: set[str] = set()
    output: list[SearchResult] = []
    for result in results:
        normalized = urllib.parse.urlsplit(result.url)._replace(query="", fragment="").geturl().rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        output.append(result)
        if len(output) >= limit:
            break
    return output


def paginate_search_payload(
    base: dict[str, Any],
    page: int,
    limit: int,
    cached: bool,
    stale: bool = False,
) -> dict[str, Any]:
    result_pool: list[SearchResult] = base["_result_pool"]
    live_urls: set[str] = base["_live_urls"]
    total_results = len(result_pool)
    total_pages = max(1, min(MAX_RESULT_PAGES, (total_results + limit - 1) // limit))
    page = min(max(1, page), total_pages)
    start = (page - 1) * limit
    results = result_pool[start:start + limit]
    page_live_count = sum(1 for result in results if result.url in live_urls)
    if not page_live_count:
        mode = "fallback"
    elif page_live_count < len(results):
        mode = "mixed"
    else:
        mode = "searxng"

    return {
        **{key: value for key, value in base.items() if not key.startswith("_")},
        "page": page,
        "mode": mode,
        "results": [asdict(result) for result in results],
        "pagination": {
            "page": page,
            "page_size": limit,
            "total_results": total_results,
            "total_pages": total_pages,
            "has_previous": page > 1,
            "has_next": page < total_pages,
        },
        "cached": cached,
        "stale": stale,
    }


def search(query: str, divergence: int, limit: int = 10, page: int = 1) -> dict[str, Any]:
    query = clean_text(query)[:160]
    divergence = max(0, min(100, int(divergence)))
    limit = max(3, min(16, int(limit)))
    page = max(1, min(10, int(page)))
    cache_key = (query, divergence, limit)
    cached = SEARCH_CACHE.get(cache_key)
    if cached and time.monotonic() - cached[0] < SEARCH_CACHE_TTL:
        return paginate_search_payload(cached[1], page, limit, True)

    plans = build_search_plans(query, divergence)
    raw: list[SearchResult] = []
    failed_plans = 0
    pool_limit = limit * MAX_RESULT_PAGES
    # One healthy semantic facet should be able to provide more than one UI
    # page when another upstream adapter is temporarily slow.
    per_plan = min(20, max(12, (pool_limit + len(plans) - 1) // len(plans) + 5))

    # Each bridge is independent. Running them concurrently keeps a high
    # divergence search close to the latency of one SearXNG request instead
    # of adding the latency of four or five requests together.
    plan_results: list[list[SearchResult]] = [[] for _ in plans]
    executor = ThreadPoolExecutor(max_workers=min(5, len(plans)), thread_name_prefix="search-plan")
    # Several public engines return an empty list for pageno > 1. Build a
    # larger candidate pool from page 1 of every semantic bridge, then page
    # that combined, deduplicated pool locally.
    futures = {executor.submit(searxng_search, plan, per_plan, 1): index for index, plan in enumerate(plans)}
    try:
        completed, pending = wait(futures, timeout=SEARCH_TOTAL_TIMEOUT)
        for future in completed:
            try:
                plan_results[futures[future]] = future.result()
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, UnicodeDecodeError, ValueError, OSError):
                failed_plans += 1
        if pending:
            failed_plans += len(pending)
            for future in pending:
                future.cancel()
    finally:
        executor.shutdown(wait=False, cancel_futures=True)
    for results_for_plan in plan_results:
        raw.extend(results_for_plan)

    live_pool = deduplicate(raw, pool_limit)
    live_count = len(live_pool)
    result_pool = live_pool
    # Curated links are an offline/demo safety net, never a way to inflate a
    # partially working live search. Normal result counts come from the
    # generic retrieval pipeline above.
    if not result_pool:
        fallback = fallback_results(plans, pool_limit, include_original=has_original_curated_results(query), page=1)
        result_pool = deduplicate(result_pool + fallback, pool_limit)

    detours = [
        {"query": plan.query, "bridge": plan.bridge, "distance": plan.distance}
        for plan in plans[1:]
    ]
    base_payload = {
        "query": query,
        "divergence": divergence,
        "page_size": limit,
        "search_backend": {
            "name": "SearXNG",
            "available": live_count > 0 or failed_plans == 0,
            "degraded": failed_plans > 0,
            "failed_plans": failed_plans,
            "message": (
                "部分实时搜索源响应超时，已展示其余来源的结果。"
                if live_count > 0 and failed_plans > 0
                else "实时搜索源暂时不可用，请稍后重试。"
                if live_count == 0 and failed_plans > 0
                else ""
            ),
        },
        "generated_at": int(time.time()),
        "plans": [asdict(plan) for plan in plans],
        "detours": detours,
        "_result_pool": result_pool,
        "_live_urls": {result.url for result in live_pool},
    }
    # Never turn a temporary upstream outage into a two-minute cached
    # zero-result page. If an older successful pool exists, serve it as stale
    # data; otherwise return a degraded response that the UI can distinguish
    # from a genuine "no matching page" result.
    if live_count == 0 and failed_plans > 0 and cached and cached[1].get("_result_pool"):
        return paginate_search_payload(cached[1], page, limit, True, stale=True)
    if live_count > 0 or failed_plans == 0:
        SEARCH_CACHE[cache_key] = (time.monotonic(), base_payload)
    if len(SEARCH_CACHE) > 128:
        cutoff = time.monotonic() - SEARCH_CACHE_TTL
        for key, (created_at, _value) in list(SEARCH_CACHE.items()):
            if created_at < cutoff:
                SEARCH_CACHE.pop(key, None)
    return paginate_search_payload(base_payload, page, limit, False)


class BeyondSearchHandler(SimpleHTTPRequestHandler):
    server_version = "BeyondSearch/0.1"

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {fmt % args}")

    def send_json(self, payload: Any, status: int = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "search_backend": "SearXNG", "searxng_url": SEARXNG_URL})
            return
        if parsed.path == "/api/search":
            params = urllib.parse.parse_qs(parsed.query)
            query = params.get("q", [""])[0].strip()
            if not query:
                self.send_json({"error": "请输入搜索内容"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                divergence = int(params.get("divergence", ["50"])[0])
                limit = int(params.get("limit", ["10"])[0])
                page = int(params.get("page", ["1"])[0])
            except ValueError:
                self.send_json({"error": "偏离度、页码和数量必须是整数"}, HTTPStatus.BAD_REQUEST)
                return
            self.send_json(search(query, divergence, limit, page))
            return
        super().do_GET()

    def do_POST(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/api/feedback":
            self.send_json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "invalid json"}, HTTPStatus.BAD_REQUEST)
            return
        feedback_file = ROOT / "feedback.jsonl"
        record = {"timestamp": int(time.time()), **payload}
        with feedback_file.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        self.send_json({"ok": True})


def main() -> None:
    server = ThreadingHTTPServer((HOST, PORT), BeyondSearchHandler)
    print(f"拓界搜索已启动：http://{HOST}:{PORT}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

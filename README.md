# 拓界搜索

拓界搜索根据用户输入和偏离度生成多个检索方向，再通过 SearXNG 聚合真实网页。SearXNG 不可用时，系统会明确切换到内置精选网址，保证演示不中断。

## 启动

当前机器需要先安装并启动 Docker Desktop。然后在本目录运行：

```powershell
docker compose -f searxng/docker-compose.yml up -d
$env:SEARXNG_URL="http://127.0.0.1:8080"
python server.py
```

访问 <http://127.0.0.1:8787/>。结果页底部会标明当前使用的是 `SearXNG 实时网页`、`SearXNG 实时网页 + 精选补充`，还是 `精选网址降级`。

如果 SearXNG 部署在其他机器，只需修改环境变量：

```powershell
$env:SEARXNG_URL="https://你的-searxng-地址"
python server.py
```

该实例必须在 `settings.yml` 的 `search.formats` 中启用 `json`。不建议把随机公共实例作为正式后端，它们通常禁用 JSON 接口或有严格限流。

## 测试

```powershell
python -m unittest -v
```

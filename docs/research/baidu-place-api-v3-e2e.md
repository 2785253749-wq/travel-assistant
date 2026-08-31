# 百度地图 Place API 3.0 与酒店 provider 对照研究

调研日期：2026-08-31
对照文件：`app/providers/baidu_hotel.py`
范围：Place API 3.0 的 `region`、`around`、`detail` 接口，以及 `status`、`results`、`result`、`total`、`uid`、`detail_info` 字段。本文只记录接口契约和当前代码行为，不记录真实 AK、完整请求 URL 或完整响应。

## 结论先行

当前 provider 的三条接口路径和主要请求参数与 Place API 3.0 基本一致：城市搜索使用 `region`，周边搜索使用 `around`，详情查询使用 `detail`；两类搜索都请求 `scope=2`，并将业务请求的页码转换为百度的 0-based `page_num`。

最重要的字段结论是：Place API 3.0 使用顶层 `results`，但不同接口形态不同——`region`/`around` 返回结果数组，`detail` 返回单个结果对象。3.0 文档没有把顶层单数 `result` 作为这三条接口的返回字段；单数 `result` 出现在百度 Place API 2.0 的详情/提示文档中。当前代码读取 `payload["results"]`，这一点与 3.0 对齐。

当前 provider 没有把 POI 级 `status`、`detail` 或 `message` 映射到酒店领域模型；它只检查顶层服务 `status`，并从 `detail_info` 提取有限字段。这是当前适配层的能力边界，不是百度字段不存在。

## 官方来源

- [百度地图开放平台：地点检索 3.0 接口文档](https://lbsyun.baidu.com/docs/webapi?title=placev3/guide/webservice-placeapiV3/interfaceDocumentV3)（页面更新时间显示为 2026-06-26；包含 `region`、`around`、`detail` 的请求和返回参数）
- [百度地图开放平台：地点检索 2.0（原检索）接口文档](https://lbsyun.baidu.com/docs/webapi?title=placev3/guide/webservice-placeapiV3/interfaceDocumentV2)（仅用于说明单数 `result` 与 3.0 `results` 的版本差异）

## 接口请求对照

| 接口 | 3.0 官方契约 | 当前 `BaiduHotelProvider` | 结论 |
| --- | --- | --- | --- |
| `region` | `query`、`region`、`ak` 必填；`region_limit=true` 才是严格限制在区域内；`region` 支持行政区划名或 citycode，最细到区县级 | `_region_params()` 发送 `query`、`region`、`region_limit=true`，并复用 `scope=2`、分页、酒店过滤、JSON 输出等公共参数 | 已对齐。`region_limit` 的严格限制语义与代码意图一致；当前未使用可选的 `type`、`center`、`address_result` 等参数 |
| `around` | `query`、`location`、`ak` 必填；`radius` 以米为单位，默认 1000；`radius_limit=true` 才是严格半径限制；`coord_type` 取值 1/2/3/4，默认 3 | `_around_params()` 发送 `query`、`location=纬度,经度`、显式 `radius`、`radius_limit=true`、`coord_type=2`，即把输入坐标声明为 GCJ-02 | 已对齐，但依赖领域请求坐标确实是 GCJ-02。当前代码没有做坐标转换 |
| `detail` | 需要 `uid` 或 `uids`，`ak` 必填；`scope=2` 返回 POI 详细信息；可选 `ret_coordtype`、`output` 等；`uids` 最多 10 个 | `get_detail()` 只发送单个 `uid`，固定 `scope=2`、`ret_coordtype=gcj02ll`、`output=json` | 单点详情已对齐；未实现官方支持的批量 `uids` |

补充：3.0 `page_num` 从 0 开始，只有请求带 `page_num` 时才会出现顶层 `total`；`page_size` 文档范围为 10～20，最大返回 20。当前领域模型把 `page_size` 限制为 1～20，provider 再用 `max(page_size, 10)` 适配百度的下限，因此正常经模型校验的请求会落在百度允许范围内。

## 返回字段对照

### 顶层 `status` 与 `message`

- 官方 3.0 文档定义顶层 `status` 为整数：成功为 `0`，失败为其他数字；`message` 是状态的英文说明，成功时为 `ok`，失败时为错误说明。
- 当前 `_check_status()` 要求存在 `status`，接受整数 `0`，也额外接受字符串 `"0"`；所有其他值统一转为本地 provider 错误。接受字符串是代码的宽容处理，非官方声明的类型。
- 当前代码没有读取 `message`，这是有意的错误信息脱敏边界：不会把上游错误原文放入领域异常或 HTTP 响应。
- POI 结果项内部还有一个同名 `status`，官方定义为营业状态字符串。它与顶层服务状态不是同一个字段。当前 `_parse_summary()` 忽略了这个 POI 级状态，领域模型也没有对应字段。

### `results` 与单数 `result`

| 版本/接口 | 官方字段形态 | 当前代码处理 |
| --- | --- | --- |
| 3.0 `region` / `around` | 顶层 `results`，结果项集合 | `search()` 要求 `results` 是 list，逐项处理 |
| 3.0 `detail` | 顶层 `results`，单个 POI 结果对象 | `get_detail()` 读取 `results`，要求 dict；对空 list 做了防御性处理并返回 `None` |
| 2.0 详情/提示文档 | 可见顶层单数 `result` | 不适用于当前使用的 `/place/v3/...` 接口 |

因此，当前代码使用 `payload["results"]` 是 3.0 正确方向；代码中的局部变量 `raw_result` 只是单个数组元素/详情对象，不代表上游存在顶层 `result` 字段。

### 结果项的 `uid`、`detail`、`detail_info`

- `uid`：官方定义为 POI 唯一标识，可用于详情检索；3.0 详情文档还特别要求应通过检索接口实时获取 POI UID 后再查详情。当前代码要求 `uid` 和 `name` 都是非空字符串，否则搜索结果项被跳过；但 `get_detail(hotel_id)` 本身不会验证该 ID 是否来自近期搜索。
- `detail`：官方定义为是否有详情页，`1` 表示有、`0` 表示没有。当前代码不读取该字段，调用详情接口前也不依据它短路；因此领域层可能对官方标记为无详情页的 POI 发起详情查询，最终由上游结果或错误决定行为。
- `detail_info`：当请求 `scope=2` 时用于承载 POI 扩展/详细信息，具体字段随 POI 类型和接口而变化。当前 `_detail_info()` 在缺失或非对象时降为空对象，因此缺失 `detail_info` 不会直接丢弃有 `uid`/`name` 的搜索结果。

当前已使用的 `detail_info` 子字段：

| 官方 3.0 字段 | 当前用途 | 对照结论 |
| --- | --- | --- |
| `overall_rating` | 映射为 `HotelSummary.rating` / `HotelDetail.rating` | 对齐；官方表中类型为 string，代码同时兼容数字 |
| `distance` | 优先映射为距离；官方明确说明圆形区域检索时返回 | 对 `around` 对齐；对 `region` 不应假定一定存在，代码允许缺失 |
| `tag`、`classified_poi_tag` | 拆分并去重为酒店标签 | 对齐 |
| `label` | 额外作为标签来源 | 在当前 3.0 `around`/搜索结果表中可见；不是当前 3.0 `detail` 返回表中列出的必备字段，因此只能作为可选兼容字段 |
| `shop_hours`、`description`、`detail_url` | 映射为营业时间、描述、详情链接 | 详情接口字段对齐，缺失时返回空值 |

## 关键差异与风险排序

1. **字段命名版本差异：低风险，当前已处理。** 3.0 的三条目标接口统一围绕 `results`；不要因旧版文档出现 `result` 而改成读取顶层 `result`。
2. **POI 营业状态未透传：中风险。** 顶层 `status=0` 只表示 API 调用成功，不表示酒店营业。若产品需要展示“暂停营业/已关闭”等状态，当前领域模型和映射都还不够。
3. **详情可用性标记未使用：中风险。** 当前忽略结果项 `detail`；这不会影响正常搜索映射，但会让详情查询不区分官方标记的有无详情页。
4. **详情 UID 新鲜度未约束：中风险。** 官方建议使用检索接口实时获取 UID；当前 provider 接受调用方直接传入的任意 `hotel_id`，没有本地来源或时效校验。
5. **`total` 上限未在适配器中显式校验：低风险。** 官方说明单次 `total` 出于数据保护最多为 150。当前代码只做非负整数清洗，不强制上限；正常上游响应不会因此超出，但契约校验并不完整。
6. **空详情形态是防御性约定：低风险。** 当前把 `results=[]` 解释为“没有该 POI”；3.0 当前详情参数表只描述单个结果对象，没有在本文引用的返回表中明确承诺空数组形态，因此这属于代码的容错行为，不应当当作官方保证。

## 只读核对范围声明

本次仅阅读官方文档、`app/providers/baidu_hotel.py`、相关酒店模型和现有 provider 测试；未调用百度生产接口，未记录任何 AK、完整请求 URL 或完整响应，未修改生产代码。

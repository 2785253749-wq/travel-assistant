(function attachExploreTrialData(globalScope) {
  const freeze = (value) => {
    if (Array.isArray(value)) value.forEach(freeze);
    else if (value && typeof value === "object") Object.values(value).forEach(freeze);
    return Object.freeze(value);
  };

  const EXPLORE_TRIAL = freeze({
    provinces: [
      { id: "fujian", name: "福建", coordinates: [118.1, 26.1], visual: "destination-visual-fujian", description: "山海相连的东南沿海省份，适合体验海岛、古城与闽南文化。", recommendation: "福建适合安排海岛、古城与闽南美食的轻松行程。" },
      { id: "yunnan", name: "云南", coordinates: [100.3, 25.3], visual: "destination-visual-yunnan", description: "高原湖泊与多民族古城交织，适合放慢节奏感受自然风光。", recommendation: "云南适合用慢节奏串联自然风光、古城与特色美食。" },
    ],
    cities: [
      {
        id: "xiamen", provinceId: "fujian", name: "厦门", coordinates: [118.09, 24.48], visual: "destination-visual-xiamen", description: "海岛、骑行与闽南小吃组成的轻松滨海城市。", recommendation: "厦门适合安排鼓浪屿、海边散步和闽南小吃。",
        places: [
          { id: "gulangyu", name: "鼓浪屿", coordinates: [118.06, 24.45], visual: "place-visual-island", description: "适合步行探索万国建筑、街巷与海景的无车小岛。", recommendation: "推荐在鼓浪屿安排半天步行，避开正午客流。" },
          { id: "nanputuo", name: "南普陀寺", coordinates: [118.10, 24.44], visual: "place-visual-temple", description: "依山而建的闽南佛教寺院，可与厦门大学周边串联游览。", recommendation: "推荐上午游览南普陀寺，再步行前往厦门大学周边。" },
          { id: "huandao-road", name: "环岛路", coordinates: [118.15, 24.43], visual: "place-visual-coast", description: "串联沙滩与海岸景观的滨海道路，适合傍晚骑行或散步。", recommendation: "推荐傍晚在环岛路骑行或散步，注意防晒与补水。" },
        ],
      },
      {
        id: "fuzhou", provinceId: "fujian", name: "福州", coordinates: [119.30, 26.08], visual: "destination-visual-fuzhou", description: "古厝街巷、山水与温泉并存的闽都城市。", recommendation: "福州适合安排三坊七巷、温泉与本地小吃。",
        places: [
          { id: "sanfang-qixiang", name: "三坊七巷", coordinates: [119.30, 26.09], visual: "place-visual-lanes", description: "保存大量明清古厝与名人故居的传统坊巷街区。", recommendation: "推荐白天慢逛三坊七巷，预留时间体验传统街区。" },
          { id: "gushan", name: "鼓山", coordinates: [119.38, 26.06], visual: "place-visual-mountain", description: "福州近郊的登高去处，可沿步道感受山林与摩崖石刻。", recommendation: "推荐上午前往鼓山，穿舒适鞋并预留往返时间。" },
          { id: "yantai-mountain", name: "烟台山", coordinates: [119.31, 26.05], visual: "place-visual-street", description: "近代建筑与文创街区交织，适合傍晚散步看夜景。", recommendation: "推荐傍晚前往烟台山，结合街区散步与夜景。" },
        ],
      },
      {
        id: "dali", provinceId: "yunnan", name: "大理", coordinates: [100.23, 25.60], visual: "destination-visual-dali", description: "苍山洱海之间的慢生活目的地，适合湖山与古城组合。", recommendation: "大理适合安排洱海、古城与苍山的慢游组合。",
        places: [
          { id: "erhai-lake", name: "洱海", coordinates: [100.23, 25.82], visual: "place-visual-lake", description: "高原湖泊与村落相映，适合选择一段湖岸慢游而非赶完全程。", recommendation: "推荐在洱海安排环湖慢游，预留拍照和休息时间。" },
          { id: "dali-ancient-city", name: "大理古城", coordinates: [100.16, 25.69], visual: "place-visual-old-town", description: "背靠苍山的古城街区，适合傍晚散步与品尝本地小吃。", recommendation: "推荐傍晚逛大理古城，品尝当地小吃。" },
          { id: "cangshan", name: "苍山", coordinates: [100.10, 25.68], visual: "place-visual-mountain", description: "横列洱海西侧的山地景观，游览前需确认天气与索道状态。", recommendation: "推荐晴天前往苍山，关注索道开放和天气变化。" },
        ],
      },
      {
        id: "lijiang", provinceId: "yunnan", name: "丽江", coordinates: [100.23, 26.87], visual: "destination-visual-lijiang", description: "古城、雪山与高原风光相邻，行程需要留出适应时间。", recommendation: "丽江适合安排古城、雪山与高原适应时间。",
        places: [
          { id: "lijiang-old-town", name: "丽江古城", coordinates: [100.23, 26.87], visual: "place-visual-old-town", description: "水系穿城、街巷密布的世界遗产古城，清晨与傍晚更适合慢逛。", recommendation: "推荐清晨或傍晚游览丽江古城，避开人流高峰。" },
          { id: "jade-dragon-snow-mountain", name: "玉龙雪山", coordinates: [100.18, 27.10], visual: "place-visual-snow", description: "高海拔雪山景区，出发前需复核天气、票务和自身身体状况。", recommendation: "推荐提前确认玉龙雪山票务，并预留高原适应时间。" },
          { id: "shuhe-ancient-town", name: "束河古镇", coordinates: [100.20, 26.92], visual: "place-visual-village", description: "相较丽江古城节奏更缓的纳西古镇，适合下午悠闲散步。", recommendation: "推荐下午游览束河古镇，体验更安静的古镇节奏。" },
        ],
      },
    ],
  });

  if (typeof module !== "undefined" && module.exports) module.exports = { EXPLORE_TRIAL };
  if (globalScope) globalScope.TRAVEL_EXPLORE_DATA = EXPLORE_TRIAL;
}(typeof window === "undefined" ? globalThis : window));

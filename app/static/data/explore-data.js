(function attachExploreTrialData(globalScope) {
  const freeze = (value) => {
    if (Array.isArray(value)) value.forEach(freeze);
    else if (value && typeof value === "object") Object.values(value).forEach(freeze);
    return Object.freeze(value);
  };

  const EXPLORE_TRIAL = freeze({
    provinces: [
      { id: "fujian", name: "福建", coordinates: [118.1, 26.1], recommendation: "福建适合安排海岛、古城与闽南美食的轻松行程。" },
      { id: "yunnan", name: "云南", coordinates: [100.3, 25.3], recommendation: "云南适合用慢节奏串联自然风光、古城与特色美食。" },
    ],
    cities: [
      {
        id: "xiamen", provinceId: "fujian", name: "厦门", coordinates: [118.09, 24.48], recommendation: "厦门适合安排鼓浪屿、海边散步和闽南小吃。",
        places: [
          { id: "gulangyu", name: "鼓浪屿", coordinates: [118.06, 24.45], recommendation: "推荐在鼓浪屿安排半天步行，避开正午客流。" },
          { id: "nanputuo", name: "南普陀寺", coordinates: [118.10, 24.44], recommendation: "推荐上午游览南普陀寺，再步行前往厦门大学周边。" },
          { id: "huandao-road", name: "环岛路", coordinates: [118.15, 24.43], recommendation: "推荐傍晚在环岛路骑行或散步，注意防晒与补水。" },
        ],
      },
      {
        id: "fuzhou", provinceId: "fujian", name: "福州", coordinates: [119.30, 26.08], recommendation: "福州适合安排三坊七巷、温泉与本地小吃。",
        places: [
          { id: "sanfang-qixiang", name: "三坊七巷", coordinates: [119.30, 26.09], recommendation: "推荐白天慢逛三坊七巷，预留时间体验传统街区。" },
          { id: "gushan", name: "鼓山", coordinates: [119.38, 26.06], recommendation: "推荐上午前往鼓山，穿舒适鞋并预留往返时间。" },
          { id: "yantai-mountain", name: "烟台山", coordinates: [119.31, 26.05], recommendation: "推荐傍晚前往烟台山，结合街区散步与夜景。" },
        ],
      },
      {
        id: "dali", provinceId: "yunnan", name: "大理", coordinates: [100.23, 25.60], recommendation: "大理适合安排洱海、古城与苍山的慢游组合。",
        places: [
          { id: "erhai-lake", name: "洱海", coordinates: [100.23, 25.82], recommendation: "推荐在洱海安排环湖慢游，预留拍照和休息时间。" },
          { id: "dali-ancient-city", name: "大理古城", coordinates: [100.16, 25.69], recommendation: "推荐傍晚逛大理古城，品尝当地小吃。" },
          { id: "cangshan", name: "苍山", coordinates: [100.10, 25.68], recommendation: "推荐晴天前往苍山，关注索道开放和天气变化。" },
        ],
      },
      {
        id: "lijiang", provinceId: "yunnan", name: "丽江", coordinates: [100.23, 26.87], recommendation: "丽江适合安排古城、雪山与高原适应时间。",
        places: [
          { id: "lijiang-old-town", name: "丽江古城", coordinates: [100.23, 26.87], recommendation: "推荐清晨或傍晚游览丽江古城，避开人流高峰。" },
          { id: "jade-dragon-snow-mountain", name: "玉龙雪山", coordinates: [100.18, 27.10], recommendation: "推荐提前确认玉龙雪山票务，并预留高原适应时间。" },
          { id: "shuhe-ancient-town", name: "束河古镇", coordinates: [100.20, 26.92], recommendation: "推荐下午游览束河古镇，体验更安静的古镇节奏。" },
        ],
      },
    ],
  });

  if (typeof module !== "undefined" && module.exports) module.exports = { EXPLORE_TRIAL };
  if (globalScope) globalScope.TRAVEL_EXPLORE_DATA = EXPLORE_TRIAL;
}(typeof window === "undefined" ? globalThis : window));

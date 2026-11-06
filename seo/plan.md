# Rosart SEO and AI-search fix plan (dry run, not applied)

Store `rosart-mhfmyuxq.myshopify.com`. Built by `tools/seo_plan.py` from the export and a read-only store snapshot; validated by `tools/seo_check.py`. Nothing here has been written to the store.
Approve this file (or strike rows) before any apply step. Character counts are (before → after).

## Summary

| Resource | Field | Changes |
| --- | --- | --- |
| shop | seo.description | 1 |
| shop | seo.title | 1 |
| page | seo.description | 3 |
| page | seo.title | 3 |
| collection | descriptionHtml | 3 |
| collection | image.altText | 3 |
| collection | seo.description | 3 |
| collection | seo.title | 3 |
| product | category | 8 |
| product | seo.description | 3 |
| product | seo.title | 14 |
| product_media | alt | 45 |
| file | alt | 10 |
| **total** | | **100** |

Kept unchanged (already pass the checker): 11 values. Decorative images (empty alt is correct, no write): 9.

## Access scopes

- Current (read 2026-11-07): `read_content, read_files, read_legal_policies, read_metaobjects, read_online_store_navigation, read_products, read_themes, write_content`
- Needed by the apply step: `write_content, write_files, write_products`
- **Missing: `write_files, write_products`**

| Mechanism | Scope | Used for |
| --- | --- | --- |
| `collectionUpdate` | `write_products` | collection descriptionHtml, collection image.altText, collection seo.description, collection seo.title |
| `fileUpdate` | `write_files` | file alt, product_media alt |
| `manual: Shopify admin > Online Store > Preferences > Homepage meta description ` | `none (admin UI)` | shop seo.description |
| `manual: Shopify admin > Online Store > Preferences > Homepage title ` | `none (admin UI)` | shop seo.title |
| `pageUpdate` | `write_content` | page seo.description, page seo.title |
| `productUpdate` | `write_products` | product category, product seo.description, product seo.title |

## Homepage (shop)

### home

`gid://shopify/Shop/107566563409`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.description | *(empty)* | Rosart grows Damask roses in its own certified organic fields in Bulgaria's Valley of Roses, then distils them and makes its rose skincare in-house. | (0 → 148) |
| seo.title | *(not readable via API)* | Organic Bulgarian Rose Skincare \| Rosart | (? → 40) |
Note: Before: Shop.description
Note: Before: the homepage title is not readable through the Admin API

## Pages

### contact

`gid://shopify/Page/763576025169`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.description | *(empty)* | Contact Rosart's Australian team with enquiries about Rosart Bulgarian rose skincare products in Australia, or for general information. | (0 → 135) |
| seo.title | *(empty)* | Contact Us, Australian Enquiries \| Rosart | (0 → 41) |

### our-collection

`gid://shopify/Page/766614831185`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.description | *(empty)* | Browse the Rosart collection of Bulgarian rose skincare: Face Care, Rose Water & Toners, and Masks & Treatments, from face creams to rose water. | (0 → 144) |
| seo.title | *(empty)* | Our Collection: Bulgarian Rose Skincare \| Rosart | (0 → 48) |

### our-story

`gid://shopify/Page/766614798417`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.description | *(empty)* | From its own certified organic Damask rose fields in Bulgaria's Valley of Roses, Rosart distils freshly harvested roses and makes its skincare in-house. | (0 → 152) |
| seo.title | *(empty)* | Our Story: From Bulgaria's Valley of Roses \| Rosart | (0 → 51) |

## Collections

### face-care-copy

`gid://shopify/Collection/705866760273`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| descriptionHtml | *(empty)* | <p>Rosart Rose Water &amp; Toners are made with Bulgarian Rosa Damascena rose water. Rose Water is a 100% natural, single-ingredient floral water in 10ml and 100ml sizes. Rose Facial Toner 330ml combines Damask rose water with botanical extracts and sodium hyaluronate.</p> | (0 → 273) |
| image.altText | *(empty)* | Rosart Rose Water 100ml with its box | (0 → 36) |
| seo.description | *(empty)* | Rosart Rose Water & Toners: 100% natural Bulgarian Rosa Damascena rose water in 10ml and 100ml, and Rose Facial Toner 330ml. Made in Bulgaria. | (0 → 142) |
| seo.title | *(empty)* | Rose Water & Toners, Bulgarian Damascena \| Rosart | (0 → 49) |

### face-care

`gid://shopify/Collection/705866661969`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| descriptionHtml | *(empty)* | <p>Rosart Face Care brings together everyday cleansing and moisturising care made with Bulgarian rose. The range includes Rose Glycerin Soap, Rose Cleansing Balm, Rose Face Soufflé Cream and Rose Face Cream. Every product is made in Bulgaria.</p> | (0 → 246) |
| image.altText | *(empty)* | Rosart Rose Face Cream with its box and rose petals | (0 → 51) |
| seo.description | *(empty)* | Rosart Face Care: Rose Face Cream, Rose Face Soufflé Cream, Rose Cleansing Balm and Rose Glycerin Soap, made in Bulgaria with Damascena rose. | (0 → 141) |
| seo.title | *(empty)* | Face Care: Rose Creams, Balm and Soap \| Rosart | (0 → 46) |

### rose-water-toners-copy

`gid://shopify/Collection/705866793041`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| descriptionHtml | *(empty)* | <p>Rosart Masks &amp; Treatments bring together rose masks and facial oil treatments, all made in Bulgaria. The range includes Rose Face Mask, the Bulgarian Rose Hydrosol Essence Mask sheet masks, Pure Face Rosehip Oil and Rose Oil Revitalizing Essence capsules.</p> | (0 → 266) |
| image.altText | *(empty)* | Rosart essence mask sachets with roses | (0 → 38) |
| seo.description | *(empty)* | Rosart Masks & Treatments: Rose Face Mask, Bulgarian Rose Hydrosol Essence sheet masks, Pure Face Rosehip Oil and Rose Oil Revitalizing Essence. | (0 → 144) |
| seo.title | *(empty)* | Masks & Treatments: Rose Masks and Oils \| Rosart | (0 → 48) |

## Products

### 100-natural-damascena-rose-water-10ml

`gid://shopify/Product/16432947331153`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Toners & Astringents > Toners (`gid://shopify/TaxonomyCategory/hb-3-2-9-17-2`) | |
| seo.title | Rosart Rose Water 10ml \| 100% Natural Bulgarian Rose Water | Damascena Rose Water 10ml, 100% Natural Toner \| Rosart | (58 → 54) |

### bulgarian-rose-hydrosol-essence-mask-5pcs

`gid://shopify/Product/16432873701457`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Skin Care Masks & Peels (`gid://shopify/TaxonomyCategory/hb-3-2-9-13`) | |
| seo.description | Discover Rosart Rose Facial Toner 330ml with Damask rose water, botanical extracts and sodium hyaluronate. Refreshes and hydrates skin. Made in Bulgaria. | Rosart Bulgarian Rose Hydrosol Essence Mask: 5 sheet masks with 93.3% organic Bulgarian Rosa Damascena flower water, to hydrate and soothe skin. | (153 → 144) |
| seo.title | Rosart Rose Facial Toner 330ml \| Hydrating Toner | Bulgarian Rose Hydrosol Sheet Mask, 5 Pack \| Rosart | (48 → 51) |

### pure-face-rosehip-oil-20ml

`gid://shopify/Product/16432953065553`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.title | Rosart Pure Face Rosehip Oil 20ml \| Nourishing Facial Oil | Pure Face Rosehip Oil 20ml, Nourishing Facial Oil \| Rosart | (57 → 58) |

### pure-face-rosehip-oil-2ml

`gid://shopify/Product/16432963649617`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.title | Rosart Pure Face Rosehip Oil 2ml \| Nourishing Facial Oil | Pure Face Rosehip Oil 2ml, Nourishing Facial Oil \| Rosart | (56 → 57) |

### rosart-rose-face-mask-80ml

`gid://shopify/Product/16432837361745`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Skin Care Masks & Peels (`gid://shopify/TaxonomyCategory/hb-3-2-9-13`) | |
| seo.title | Rosart Rose Face Mask 80ml \| Hydrating Facial Mask | Rose Face Mask 80ml, Hydrating Facial Mask \| Rosart | (50 → 51) |

### rose-cleansing-balm-80ml

`gid://shopify/Product/16432829792337`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Facial Cleansers (`gid://shopify/TaxonomyCategory/hb-3-2-9-6`) | |
| seo.description | Discover Rosart Rose Cleansing Balm with rosehip, jojoba and avocado oils. Gently removes makeup and impurities while leaving skin soft and nourished. Made in Bulgaria. | Rosart Rose Cleansing Balm 80ml with rosehip, jojoba and avocado oils gently removes makeup and impurities, leaving skin soft. Made in Bulgaria. | (168 → 144) |
| seo.title | Rosart Rose Cleansing Balm 80ml \| Makeup Remover | Rose Cleansing Balm 80ml, Makeup Remover \| Rosart | (48 → 49) |

### rose-face-cream-3g

`gid://shopify/Product/16431958523985`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.title | Rosart Rose Face Cream 3g \| Hydrating Facial Moisturizer | Rose Face Cream 3g, Nourishing Moisturizer \| Rosart | (56 → 51) |

### rose-face-cream-50ml

`gid://shopify/Product/16431658401873`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.title | Rosart Rose Face Cream 50ml \| Bulgarian Rose Skincare | Rose Face Cream 50ml, Nourishing Moisturizer \| Rosart | (53 → 53) |

### rose-face-souffle-cream-3g

`gid://shopify/Product/16431961309265`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.title | Rosart Rose Face Soufflé Cream 3g \| Lightweight Moisturiser | Rose Face Soufflé Cream 3g, Lightweight Moisturiser \| Rosart | (59 → 60) |

### rose-face-souffle-cream-50ml

`gid://shopify/Product/16431960621137`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| seo.description | Discover Rosart Rose Face Soufflé Cream 50ml. A lightweight facial moisturizer with Damascena rose water, rosehip oil and hyaluronic acid for soft, hydrated skin. | Rosart Rose Face Soufflé Cream 50ml: a lightweight facial moisturizer with Damascena rose flower water, rose oil and rosehip oil. Made in Bulgaria. | (162 → 147) |
| seo.title | Rosart Rose Face Soufflé Cream 50ml \| Lightweight Moisturizer | Rose Face Soufflé Cream 50ml, Light Moisturizer \| Rosart | (61 → 56) |

### rose-facial-toner-330ml

`gid://shopify/Product/16432859578449`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Toners & Astringents > Toners (`gid://shopify/TaxonomyCategory/hb-3-2-9-17-2`) | |
| seo.title | Rosart Rose Facial Toner 330ml \| Hydrating Toner | Rose Facial Toner 330ml with Damask Rose Water \| Rosart | (48 → 55) |

### rose-glycerin-soap-90g

`gid://shopify/Product/16432893689937`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Bath & Body > Bar Soap (`gid://shopify/TaxonomyCategory/hb-3-2-1-1`) | |
| seo.title | Rosart Rose Glycerin Soap 90g \| Handmade Rose Soap | Rose Glycerin Soap 90g, Handmade with Rose Petals \| Rosart | (50 → 58) |

### rose-oil-revitalizing-essence-30-capsules

`gid://shopify/Product/16432900898897`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Serums (`gid://shopify/TaxonomyCategory/hb-3-2-9-21`) | |
| seo.title | Rosart Rose Oil Revitalizing Essence \| 30 Capsules | Rose Oil Revitalizing Essence, 30 Capsules \| Rosart | (50 → 51) |

### rose-water-100ml

`gid://shopify/Product/16432922165329`

| Field | Before | After | Chars |
| --- | --- | --- | --- |
| category | Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Moisturizers (`gid://shopify/TaxonomyCategory/hb-3-2-9-20`) | Health & Beauty > Personal Care > Cosmetics > Skin Care > Toners & Astringents > Toners (`gid://shopify/TaxonomyCategory/hb-3-2-9-17-2`) | |
| seo.title | Rosart Rose Water 100ml \| 100% Natural Bulgarian Rose Water | Damascena Rose Water 100ml, 100% Natural Toner \| Rosart | (59 → 55) |

## Product images (alt text)

### 100-natural-damascena-rose-water-10ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartBulgarianRoseWaterSet.png | `gid://shopify/MediaImage/74419497762897` | *(empty)* | 100% Natural Damascena Rose Water 10ml, with its box | (0 → 52) |
| ESPRITHONGKONG_3.png | `gid://shopify/MediaImage/74452926726225` | *(empty)* | 100% Natural Damascena Rose Water 10ml, among roses in a garden | (0 → 63) |

### bulgarian-rose-hydrosol-essence-mask-5pcs



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartBulgarianRoseEssenceMaskBox.png | `gid://shopify/MediaImage/74419025838161` | *(empty)* | Bulgarian Rose Hydrosol Essence Mask 5pcs, front of the box | (0 → 59) |
| RosartBulgarianRoseMaskShowcase.png | `gid://shopify/MediaImage/74454394142801` | *(empty)* | Bulgarian Rose Hydrosol Essence Mask 5pcs, box and sachet among roses | (0 → 69) |
| RST_007bac73-3152-441e-9d95-86ccd6e85270.png | `gid://shopify/MediaImage/74453937946705` | *(empty)* | Bulgarian Rose Hydrosol Essence Mask 5pcs, sachet held by a model in a rose field | (0 → 81) |
| RST2.png | `gid://shopify/MediaImage/74454394110033` | *(empty)* | Bulgarian Rose Hydrosol Essence Mask 5pcs, two sachets with roses | (0 → 65) |

### pure-face-rosehip-oil-20ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Rosart_Rosehip_4x5_2.png | `gid://shopify/MediaImage/74419533742161` | *(empty)* | Pure Face Rosehip Oil 20ml, with its box | (0 → 40) |
| RosartRosehipOilinaSunlitGarden.png | `gid://shopify/MediaImage/74419533807697` | *(empty)* | Pure Face Rosehip Oil 20ml, with its box among roses in a garden | (0 → 64) |
| Rosart_Rosehip_4x5_3.png | `gid://shopify/MediaImage/74419533774929` | *(empty)* | Pure Face Rosehip Oil 20ml, with roses and a ribbon | (0 → 51) |
| Rosart_Rosehip_4x5_1.png | `gid://shopify/MediaImage/74419533840465` | *(empty)* | Pure Face Rosehip Oil 20ml, front view | (0 → 38) |

### pure-face-rosehip-oil-2ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartSmallDropperBottleinWarmStudioLight.png | `gid://shopify/MediaImage/74419602587729` | *(empty)* | Pure Face Rosehip Oil 2ml, front view | (0 → 37) |

### rosart-rose-face-mask-80ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartBulgarianRoseFaceMaskDuo.png | `gid://shopify/MediaImage/74418718212177` | *(empty)* | Rose Face Mask 80ml, with its box and spoon | (0 → 43) |
| RosartRoseMaskGardenStillLife.png | `gid://shopify/MediaImage/74418718244945` | *(empty)* | Rose Face Mask 80ml, with a rose | (0 → 32) |
| RosartRoseMaskinBloom.png | `gid://shopify/MediaImage/74418718277713` | *(empty)* | Rose Face Mask 80ml, held by a model among roses | (0 → 48) |

### rose-cleansing-balm-80ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartRoseCleansingBalmDisplay.png | `gid://shopify/MediaImage/74418652086353` | *(empty)* | Rose Cleansing Balm 80ml, with its box | (0 → 38) |
| RosartRoseBalminaSunlitGarden.png | `gid://shopify/MediaImage/74418652151889` | *(empty)* | Rose Cleansing Balm 80ml, with its box among roses | (0 → 50) |
| SunlitRoseCleansingBalmPortrait.png | `gid://shopify/MediaImage/74418652119121` | *(empty)* | Rose Cleansing Balm 80ml, held by a model in a rose field | (0 → 57) |

### rose-face-cream-3g



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| BulgarianRoseCream3g_14a773ff-e665-4af0-8662-6debcfef1347.jpg | `gid://shopify/MediaImage/74412707905617` | *(empty)* | Rose Face Cream 3g, with its box | (0 → 32) |
| BulgarianRoseFaceCreamStillLife_ca18a3dc-fd34-4fcd-90fa-796fa6294fe9.png | `gid://shopify/MediaImage/74412708659281` | *(empty)* | Rose Face Cream 3g, with its box and rose petals | (0 → 48) |

### rose-face-cream-50ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| BulgarianRoseCreamDuo.jpg | `gid://shopify/MediaImage/74412680839249` | *(empty)* | Rose Face Cream 50ml, with its box and spoon | (0 → 44) |
| ESPRITHONGKONG_1.png | `gid://shopify/MediaImage/74412645941329` | *(empty)* | Rose Face Cream 50ml, with its box among roses in a garden | (0 → 58) |
| fc1.png | `gid://shopify/MediaImage/74412759351377` | *(empty)* | Rose Face Cream 50ml, with a rose and petals | (0 → 44) |
| SunlitRoseFaceCreamPortrait.png | `gid://shopify/MediaImage/74412600852561` | *(empty)* | Rose Face Cream 50ml, held by a model in a rose field | (0 → 53) |

### rose-face-souffle-cream-3g



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartRoseSouffleCreamSet.png | `gid://shopify/MediaImage/74412718358609` | *(empty)* | Rose Face Soufflé Cream 3g, with its box | (0 → 40) |

### rose-face-souffle-cream-50ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Curved-LabelRosartRoseCreamSet.png | `gid://shopify/MediaImage/74451420545105` | *(empty)* | Rose Face Soufflé Cream 50ml, with its box and spoon | (0 → 52) |
| RosartRoseSouffleStillLife.png | `gid://shopify/MediaImage/74412714033233` | *(empty)* | Rose Face Soufflé Cream 50ml, with its box among roses | (0 → 54) |

### rose-facial-toner-330ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RST_65e67bcf-5d90-4538-8ea6-8383481d6367.png | `gid://shopify/MediaImage/74455763026001` | *(empty)* | Rose Facial Toner 330ml, with its box | (0 → 37) |
| RosartRoseTonerStillLife.png | `gid://shopify/MediaImage/74418953715793` | *(empty)* | Rose Facial Toner 330ml, with a rose | (0 → 36) |
| 1_e8d51abc-fa25-4a53-8000-2a109dc374e9.png | `gid://shopify/MediaImage/74418953650257` | *(empty)* | Rose Facial Toner 330ml, held in a hand among roses | (0 → 51) |
| 2_3f29349c-2ca4-4df1-a6a4-11f29261cce9.png | `gid://shopify/MediaImage/74418953683025` | *(empty)* | Rose Facial Toner 330ml, held up by a model in a rose field | (0 → 59) |

### rose-glycerin-soap-90g



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| RosartRoseGlycerinSoapSet.png | `gid://shopify/MediaImage/74419133218897` | *(empty)* | Rose Glycerin Soap 90g, with its box | (0 → 36) |
| Rose-GoldGlycerinSoapPackaging.png | `gid://shopify/MediaImage/74419133284433` | *(empty)* | Rose Glycerin Soap 90g, with its box and roses | (0 → 46) |
| RosartRoseSoapinSunlitGarden_3f7fc8b5-0d9f-477b-88d5-ba98bfe3eaa7.png | `gid://shopify/MediaImage/74432052363345` | *(empty)* | Rose Glycerin Soap 90g, with its box in a sunlit garden | (0 → 55) |
| SunlitRoseGardenwithAmberDisc.png | `gid://shopify/MediaImage/74419133317201` | *(empty)* | Rose Glycerin Soap 90g, held up by a model in a rose field | (0 → 58) |

### rose-oil-revitalizing-essence-30-capsules



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| GlossyGoldenSoftgelCapsules.png | `gid://shopify/MediaImage/74419416039505` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, with its box and loose capsules | (0 → 74) |
| RST_1_c9e99047-71cb-42e8-86ac-bfdbad6c3da8.png | `gid://shopify/MediaImage/74453893972049` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, capsules in an open container among roses | (0 → 84) |
| RST_a7c30e84-49fb-48b6-a53d-de7ec63c08d5.png | `gid://shopify/MediaImage/74453894004817` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, open container with a capsule on a spoon | (0 → 83) |
| RosartGoldSkincareJarinBloomingGarden.png | `gid://shopify/MediaImage/74419241287761` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, container held in a hand among roses | (0 → 79) |
| SunlitRoseGardenSkincarePortrait.png | `gid://shopify/MediaImage/74419230474321` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, held by a model in a rose field | (0 → 74) |
| GoldenCapsuleCuttingGuide.png | `gid://shopify/MediaImage/74419382222929` | *(empty)* | Rose Oil Revitalizing Essence 30 Capsules, how to use: cut open the capsule with the mini scissors, squeeze out the essence, massage onto face and neck | (0 → 151) |

### rose-water-100ml



| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| BulgarianRoseRosartWaterSet.png | `gid://shopify/MediaImage/74419325010001` | *(empty)* | 100% Natural Damascena Rose Water 100ml, with its box | (0 → 53) |
| RST.png | `gid://shopify/MediaImage/74453223735377` | *(empty)* | 100% Natural Damascena Rose Water 100ml, with roses | (0 → 51) |
| RST_2.png | `gid://shopify/MediaImage/74453223800913` | *(empty)* | 100% Natural Damascena Rose Water 100ml, among roses in a garden | (0 → 64) |
| RST_1.png | `gid://shopify/MediaImage/74453223768145` | *(empty)* | 100% Natural Damascena Rose Water 100ml, held in a hand among roses | (0 → 67) |
| RosartRoseWaterinaSunlitRoseGarden.png | `gid://shopify/MediaImage/74419427278929` | *(empty)* | 100% Natural Damascena Rose Water 100ml, held by a model in a rose field | (0 → 72) |

## Theme images in Shopify Files (alt text)

### ARTEC.png

`gid://shopify/MediaImage/74438877937745`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| ARTEC.png | `gid://shopify/MediaImage/74438877937745` | *(empty)* | Artec Chemical Group | (0 → 20) |

### Add_a_heading_fcec8aa5-9bda-44ce-96bc-7966b68e00f2.png

`gid://shopify/MediaImage/74433948680273`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Add_a_heading_fcec8aa5-9bda-44ce-96bc-7966b68e00f2.png | `gid://shopify/MediaImage/74433948680273` | *(empty)* | Our Story | (0 → 9) |

### ESPRIT_HONG_KONG.png

`gid://shopify/MediaImage/74389395308625`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| ESPRIT_HONG_KONG.png | `gid://shopify/MediaImage/74389395308625` | *(empty)* | Rosart | (0 → 6) |

### ESPRIT_HONG_KONG_f0a060ae-af0e-4c30-b417-84e0f08f40c7.png

`gid://shopify/MediaImage/74392770183249`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| ESPRIT_HONG_KONG_f0a060ae-af0e-4c30-b417-84e0f08f40c7.png | `gid://shopify/MediaImage/74392770183249` | *(empty)* | Pickers harvesting roses in a rose field | (0 → 40) |

### ORIZEN.png

`gid://shopify/MediaImage/74438877741137`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| ORIZEN.png | `gid://shopify/MediaImage/74438877741137` | *(empty)* | ORIZEN Australia | (0 → 16) |

### ROSART.png

`gid://shopify/MediaImage/74438877970513`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| ROSART.png | `gid://shopify/MediaImage/74438877970513` | *(empty)* | Rosart Bulgaria | (0 → 15) |

### Rosart_Distillery_in_Bulgaria_s_Valley_of_Roses.png

`gid://shopify/MediaImage/74433281982545`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Rosart_Distillery_in_Bulgaria_s_Valley_of_Roses.png | `gid://shopify/MediaImage/74433281982545` | *(empty)* | Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the heart of Bulgaria's Valley of Roses | (0 → 127) |

### Rosart_Rose_Balm_in_a_Sunlit_Garden.png

`gid://shopify/MediaImage/74409211494481`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Rosart_Rose_Balm_in_a_Sunlit_Garden.png | `gid://shopify/MediaImage/74409211494481` | *(empty)* | Rosart Rose Cleansing Balm with its box among roses | (0 → 51) |

### Rosart_s_Bulgarian_Valley_of_Roses_Map.png

`gid://shopify/MediaImage/74433807286353`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| Rosart_s_Bulgarian_Valley_of_Roses_Map.png | `gid://shopify/MediaImage/74433807286353` | *(empty)* | Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the heart of Bulgaria's Valley of Roses | (0 → 127) |

### USDA_and_EU_Organic_Certifications.png

`gid://shopify/MediaImage/74392596611153`

| File | Media gid | Before | After | Chars |
| --- | --- | --- | --- | --- |
| USDA_and_EU_Organic_Certifications.png | `gid://shopify/MediaImage/74392596611153` | *(empty)* | USDA and EU Organic Certification: Ceres certified, EU organic, USDA Organic and GMP certified | (0 → 94) |

## Decorative images (no write: empty alt is correct)

| File | gid | Why |
| --- | --- | --- |
| 1.png | `gid://shopify/MediaImage/74391738908753` | homepage collection tile; the caption 'Face Care' beside it names the link (site renders alt="") |
| 2.png | `gid://shopify/MediaImage/74391743365201` | homepage collection tile; the caption 'Rose Water & Toners' names the link (site renders alt="") |
| 3.png | `gid://shopify/MediaImage/74391749230673` | homepage collection tile; the caption 'Masks & Treatments' names the link (site renders alt="") |
| DSC06467.jpg | `gid://shopify/MediaImage/74391044358225` | Our Story mood photo (roses in a scale pan) beside the distillation text |
| DSC08666_Rosart_Mobile_1200x1500_e230edb1-8068-47e5-b216-efa8170e4f7e.jpg | `gid://shopify/MediaImage/74433116078161` | homepage hero (mobile), mood photo under the same heading |
| Untitled_design_7_f6c26f2b-ad40-440e-a57a-fb6da4fe0dcb.png | `gid://shopify/MediaImage/74393615335505` | homepage hero (desktop); the overlaid heading 'From Soil to Skin' carries the message |
| Untitled_design_8_7171c05c-c755-42c7-b662-fc397c08f2bb.png | `gid://shopify/MediaImage/74392448303185` | Our Story landscape banner (rose field), no information |
| bg1.png | `gid://shopify/MediaImage/74409321889873` | section background (blurred roses) |
| bg3.png | `gid://shopify/MediaImage/74409847423057` | section background (rose petal texture) |

## Kept values (no change; validated)

| Resource | Handle | Field | Value | Chars |
| --- | --- | --- | --- | --- |
| product | 100-natural-damascena-rose-water-10ml | seo.description | Discover Rosart Rose Water 10ml, made from 100% natural Bulgarian Rosa Damascena flower water. Gently soothes, hydrates and refreshes skin. | 139 |
| product | pure-face-rosehip-oil-20ml | seo.description | Discover Rosart Pure Face Rosehip Oil 20ml with organic rosehip oil, Damask rose oil and vitamin E. Nourishes skin and improves the appearance of dullness. | 155 |
| product | pure-face-rosehip-oil-2ml | seo.description | Discover Rosart Pure Face Rosehip Oil 2ml with organic rosehip oil, Damask rose oil and vitamin E. Nourishes skin and improves the appearance of dullness. | 154 |
| product | rosart-rose-face-mask-80ml | seo.description | Discover Rosart Rose Face Mask with Bulgarian rose water, rose oil and marine polysaccharides. Hydrates, refreshes and softens skin. Made in Bulgaria. | 150 |
| product | rose-face-cream-3g | seo.description | Discover Rosart Rose Face Cream 3g, enriched with organic rose oil, rose water and rose concrete. A nourishing facial moisturizer for day and night use. | 152 |
| product | rose-face-cream-50ml | seo.description | Discover Rosart Rose Face Cream 50ml, formulated with organic rose oil, rose water and rose concrete to hydrate and nourish skin. For day and night use. | 152 |
| product | rose-face-souffle-cream-3g | seo.description | Discover Rosart Rose Face Soufflé Cream 3g, a lightweight moisturiser with rose water, botanical oils and sodium hyaluronate. Made in Bulgaria. | 143 |
| product | rose-facial-toner-330ml | seo.description | Discover Rosart Rose Facial Toner 330ml with Damask rose water, botanical extracts and sodium hyaluronate. Refreshes and hydrates skin. Made in Bulgaria. | 153 |
| product | rose-glycerin-soap-90g | seo.description | Discover Rosart Rose Glycerin Soap 90g, handmade with Bulgarian rose water, rose petals and glycerin to gently cleanse and soften skin. Made in Bulgaria. | 153 |
| product | rose-oil-revitalizing-essence-30-capsules | seo.description | Discover Rosart Rose Oil Revitalizing Essence with rose oils, rosehip, grape seed oil and vitamin E. 30 facial oil capsules, made in Bulgaria. | 142 |
| product | rose-water-100ml | seo.description | Discover Rosart Rose Water 100ml, made from 100% natural Bulgarian Rosa Damascena flower water. Gently soothes, hydrates and refreshes skin. | 140 |

## Store-level findings (report only: owner decisions, not in this plan)

1. **Hydrosol Essence Mask carries the toner's details**: The four custom metafields of `bulgarian-rose-hydrosol-essence-mask-5pcs` (how_to_use, the_ingredients, certifications_brand_values, caution) are the Rose Facial Toner's text: 'Pour an appropriate amount onto a cotton pad...', '...the toner's botanical formula'. The site shows these on the mask page. This plan fixes only the mask's SEO (also the toner's) and does not touch verbatim metafield text.
   Evidence: export/shopify/products.json, product gid://shopify/Product/16432873701457
   Decision needed: the owner supplies the mask's own how-to-use, ingredients (INCI) and caution from its packaging
2. **Another business named as the site operator**: 'MinervaBelle & Co.' is named on the Contact page and as the operator in the Privacy policy and Terms of service; the Contact page and Privacy policy give info@deniva.com.au (Deniva), not info@rosart.com.au. The site's dist check rejects any email other than the enquiry email.
   Evidence: pages.json contact bodyHtml; shopPolicies PRIVACY_POLICY and TERMS_OF_SERVICE (read 2026-11-07)
   Decision needed: which business name and email Rosart's Australian site should show
3. **Policies**: Privacy policy and Terms of service exist; refund, shipping and contact-information policies do not (the site sells nothing, so refund/shipping may be N/A). The Privacy policy says the website is 'hosted on Shopify' and mentions a contact form; the static site is on S3/CloudFront and has no form. The static site's URL map has no /policies/ pages, so neither policy is published.
   Decision needed: policy set to publish on the static site, and updated hosting wording
4. **Product category is 'Face Moisturizers' on all 14 products**: Planned: toner and both rose waters to Toners, both masks to Skin Care Masks & Peels, balm to Facial Cleansers, soap to Bar Soap, capsules to Face Serums (8 changes). Creams stay Face Moisturizers. Rosehip oil (type 'Facial Oil') is left as Face Moisturizers: the taxonomy has no face-oil leaf; Face Serums is the alternative.
   Decision needed: rosehip oil category (keep Face Moisturizers or move to Face Serums)
5. **Homepage SEO does not reach the static site**: Shop.description is empty and the homepage title is unset. The static site does not read either: src/pages/index.astro builds the title as 'Rosart | <footer text>' and the description from the homepage sections, and the export does not capture Shop.description. After the owner sets them in Online Store > Preferences, the exporter and index page need a change to use them (other agent's files; not edited here).
   Decision needed: whether the site should take its homepage title/description from Shopify
6. **Hard-coded image alt in the site**: src/site.config.ts `imageAlt` holds 5 alt texts. This plan writes them (plus 5 more) to the Shopify file alt, which the export already reads (files.json alt). After apply and re-export, `imageAlt` can be emptied (other agent's file; not edited here).
7. **Theme social links are placeholders**: Footer social links point to bare https://www.facebook.com/, https://www.instagram.com/, https://www.tiktok.com/, https://x.com/, https://www.youtube.com/.
   Evidence: theme-content.json footer-group social-links block
   Decision needed: real profile URLs, or remove the block
8. **Homepage hero button goes to the wrong place**: The hero button 'Discover Our Story' links to shopify://collections/all (the catalogue), not the Our Story page.
   Evidence: theme-content.json templates/index.json section 0, block 2
   Decision needed: link to /pages/our-story or relabel
9. **Collection handles do not match titles**: 'Rose Water & Toners' has handle face-care-copy and 'Masks & Treatments' has handle rose-water-toners-copy. The static site slugs collections by title, so URLs are fine; handles are not changed by this plan.
10. **Product images that may mislead**: Rose Oil Revitalizing Essence image RST_1_c9e99047-...png shows a box labelled 'Rose Face Cream 50 ML ... with rose essential oil & vitamin E capsules'. Mask image RST2.png and theme tile 3.png show sachets reading 'Bulgarian Rose Water Essence Mask' with garbled text (look AI-generated); fc1.png and RST_1.png labels are also garbled. The 2ml rosehip oil's only image shows no size or product name. Several theme files are named ESPRIT_HONG_KONG*.png (leftover filenames; content is Rosart).
   Decision needed: replace or keep these images
11. **Metafield formatting**: Literal markdown asterisks in multi-line metafields: '**Full Ingredients (INCI)**' (Rose Cleansing Balm the_ingredients) and '**Organic Rose Oil**' etc. (Rose Face Cream 3g the_ingredients). Rose Cleansing Balm certifications_brand_values repeats 'No artificial fragrance added.' twice. Verbatim source text, not changed here.
   Decision needed: clean up in the admin
12. **Certification marks**: The homepage certification image shows CERES, EU organic, USDA Organic and GMP marks; product metafields say certification scope 'is subject to the manufacturer's documentation'.
   Decision needed: confirm each mark is held for the products shown (claims with legal weight)
13. **Store basics**: Shop primary domain is still rosart-mhfmyuxq.myshopify.com (fine while the static site is the public face); store contact email is share@solagratia.com.au; the 'Your Privacy Choices' (data-sharing-opt-out) page and footer 'Search' item are Shopify defaults the site drops. Related links on Our Story (artechem.com, rosart-bg.com, orizen.com.au) are the parent company, brand origin and retail partner, not stray businesses.
14. **Tags**: Every product carries the tag 'Face Care', including rose waters, oils, masks and capsules.
   Decision needed: optional tidy-up (tags do not drive collections here: collections are manual)

## API references (shopify.dev, Admin GraphQL 2026-10)

- Product SEO: `productUpdate` with `ProductUpdateInput.seo` (`SEOInput {title, description}`); stored as `global.title_tag` / `global.description_tag`. Requires `write_products`. https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductUpdateInput, https://shopify.dev/docs/apps/build/marketing/optimize-storefront-seo
- Product category: `ProductUpdateInput.category: ID` (TaxonomyCategory gid); ids from `taxonomy { categories(search:) }`. https://shopify.dev/docs/api/admin-graphql/2026-10/queries/taxonomy
- Image alt: `fileUpdate(files: [{id, alt}])` on the MediaImage gid; requires `write_files` (or `write_themes`). `productUpdateMedia` is deprecated. Alt lives on the file, so read it back after writing (checklist section 3). https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/fileUpdate
- Collections: `collectionUpdate(collection: CollectionUpdateInput)` with `descriptionHtml`, `seo` and `image: ImageInput {id, altText}`; requires `write_products`. https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/collectionUpdate
- Pages: `PageUpdateInput` has no `seo` field; SEO is the `global.title_tag` / `global.description_tag` metafields via `pageUpdate(id, page: {metafields})`; requires `write_content` or `write_online_store_pages`. https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/PageUpdateInput
- Homepage: `Shop.description` is 'the shop's meta description used in search engine results' and is read-only; no Admin API mutation sets it or the homepage title, so both are set in Online Store > Preferences. https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Shop

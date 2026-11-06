"""Authored values for the Rosart SEO plan (tools/seo_plan.py), all facts from the store's own text.

Every value here is checked by tools/seo_check.py against its entry's sources (product title,
description, custom metafields; collection members; page and theme text; what an image shows).
Image observations come from viewing every image in export/images/ on 2026-11-07; alt text names
only what the product title supports plus a neutral view (no materials, colours or claims).
Product fields not listed keep their current value (the checker still validates them).
"""
from __future__ import annotations

TONERS = "gid://shopify/TaxonomyCategory/hb-3-2-9-17-2"
MASKS = "gid://shopify/TaxonomyCategory/hb-3-2-9-13"
CLEANSERS = "gid://shopify/TaxonomyCategory/hb-3-2-9-6"
BAR_SOAP = "gid://shopify/TaxonomyCategory/hb-3-2-1-1"
SERUMS = "gid://shopify/TaxonomyCategory/hb-3-2-9-21"

CONTENT = {
    "categories": {
        TONERS: "Health & Beauty > Personal Care > Cosmetics > Skin Care > Toners & Astringents > Toners",
        MASKS: "Health & Beauty > Personal Care > Cosmetics > Skin Care > Skin Care Masks & Peels",
        CLEANSERS: "Health & Beauty > Personal Care > Cosmetics > Skin Care > Facial Cleansers",
        BAR_SOAP: "Health & Beauty > Personal Care > Cosmetics > Bath & Body > Bar Soap",
        SERUMS: "Health & Beauty > Personal Care > Cosmetics > Skin Care > Face Serums",
    },
    "shop": {
        "seo.title": "Organic Bulgarian Rose Skincare | Rosart",
        "seo.description": (
            "Rosart grows Damask roses in its own certified organic fields in Bulgaria's Valley of Roses, "
            "then distils them and makes its rose skincare in-house."
        ),
    },
    "pages": {
        "our-story": {
            "seo.title": "Our Story: From Bulgaria's Valley of Roses | Rosart",
            "seo.description": (
                "From its own certified organic Damask rose fields in Bulgaria's Valley of Roses, Rosart "
                "distils freshly harvested roses and makes its skincare in-house."
            ),
        },
        "our-collection": {
            "seo.title": "Our Collection: Bulgarian Rose Skincare | Rosart",
            "seo.description": (
                "Browse the Rosart collection of Bulgarian rose skincare: Face Care, Rose Water & Toners, "
                "and Masks & Treatments, from face creams to rose water."
            ),
        },
        "contact": {
            "seo.title": "Contact Us, Australian Enquiries | Rosart",
            "seo.description": (
                "Contact Rosart's Australian team with enquiries about Rosart Bulgarian rose skincare "
                "products in Australia, or for general information."
            ),
        },
    },
    "collections": {
        "face-care": {
            "descriptionHtml": (
                "<p>Rosart Face Care brings together everyday cleansing and moisturising care made with "
                "Bulgarian rose. The range includes Rose Glycerin Soap, Rose Cleansing Balm, Rose Face "
                "Soufflé Cream and Rose Face Cream. Every product is made in Bulgaria.</p>"
            ),
            "seo.title": "Face Care: Rose Creams, Balm and Soap | Rosart",
            "seo.description": (
                "Rosart Face Care: Rose Face Cream, Rose Face Soufflé Cream, Rose Cleansing Balm and Rose "
                "Glycerin Soap, made in Bulgaria with Damascena rose."
            ),
            "image.altText": "Rosart Rose Face Cream with its box and rose petals",
        },
        "face-care-copy": {
            "descriptionHtml": (
                "<p>Rosart Rose Water &amp; Toners are made with Bulgarian Rosa Damascena rose water. Rose "
                "Water is a 100% natural, single-ingredient floral water in 10ml and 100ml sizes. Rose "
                "Facial Toner 330ml combines Damask rose water with botanical extracts and sodium hyaluronate.</p>"
            ),
            "seo.title": "Rose Water & Toners, Bulgarian Damascena | Rosart",
            "seo.description": (
                "Rosart Rose Water & Toners: 100% natural Bulgarian Rosa Damascena rose water in 10ml and "
                "100ml, and Rose Facial Toner 330ml. Made in Bulgaria."
            ),
            "image.altText": "Rosart Rose Water 100ml with its box",
        },
        "rose-water-toners-copy": {
            "descriptionHtml": (
                "<p>Rosart Masks &amp; Treatments bring together rose masks and facial oil treatments, all made in "
                "Bulgaria. The range includes "
                "Rose Face Mask, the Bulgarian Rose Hydrosol Essence Mask sheet masks, Pure Face Rosehip Oil "
                "and Rose Oil Revitalizing Essence capsules.</p>"
            ),
            "seo.title": "Masks & Treatments: Rose Masks and Oils | Rosart",
            "seo.description": (
                "Rosart Masks & Treatments: Rose Face Mask, Bulgarian Rose Hydrosol Essence sheet masks, "
                "Pure Face Rosehip Oil and Rose Oil Revitalizing Essence."
            ),
            "image.altText": "Rosart essence mask sachets with roses",
        },
    },
    "collection_image_observations": {
        "face-care": "jar and box beside a rose and petals; box text: BULGARIAN ROSE, Rosart, Rose Face Cream, 3g",
        "face-care-copy": "bottle beside its box; box text: Rosart, Rose Water, 100 ML",
        "rose-water-toners-copy": "two mask sachets with roses; sachet text: Bulgarian Rose Water Essence Mask (partly garbled)",
    },
    "products": {
        "100-natural-damascena-rose-water-10ml": {
            "seo.title": "Damascena Rose Water 10ml, 100% Natural Toner | Rosart",
            "alt": {
                "RosartBulgarianRoseWaterSet.png": "with its box",
                "ESPRITHONGKONG_3.png": "among roses in a garden",
            },
            "category": TONERS,
        },
        "rose-water-100ml": {
            "seo.title": "Damascena Rose Water 100ml, 100% Natural Toner | Rosart",
            "alt": {
                "BulgarianRoseRosartWaterSet.png": "with its box",
                "RST.png": "with roses",
                "RST_2.png": "among roses in a garden",
                "RST_1.png": "held in a hand among roses",
                "RosartRoseWaterinaSunlitRoseGarden.png": "held by a model in a rose field",
            },
            "category": TONERS,
        },
        "bulgarian-rose-hydrosol-essence-mask-5pcs": {
            # Its custom metafields carry the Rose Facial Toner's text (a store finding), so only the
            # mask's own title and description are used as sources.
            "exclude_metafields": True,
            "seo.title": "Bulgarian Rose Hydrosol Sheet Mask, 5 Pack | Rosart",
            "seo.description": (
                "Rosart Bulgarian Rose Hydrosol Essence Mask: 5 sheet masks with 93.3% organic Bulgarian "
                "Rosa Damascena flower water, to hydrate and soothe skin."
            ),
            "alt": {
                "RosartBulgarianRoseEssenceMaskBox.png": "front of the box",
                "RosartBulgarianRoseMaskShowcase.png": "box and sachet among roses",
                "RST_007bac73-3152-441e-9d95-86ccd6e85270.png": "sachet held by a model in a rose field",
                "RST2.png": "two sachets with roses",
            },
            "category": MASKS,
        },
        "pure-face-rosehip-oil-20ml": {
            "seo.title": "Pure Face Rosehip Oil 20ml, Nourishing Facial Oil | Rosart",
            "alt": {
                "Rosart_Rosehip_4x5_2.png": "with its box",
                "RosartRosehipOilinaSunlitGarden.png": "with its box among roses in a garden",
                "Rosart_Rosehip_4x5_3.png": "with roses and a ribbon",
                "Rosart_Rosehip_4x5_1.png": "front view",
            },
        },
        "pure-face-rosehip-oil-2ml": {
            "seo.title": "Pure Face Rosehip Oil 2ml, Nourishing Facial Oil | Rosart",
            "alt": {"RosartSmallDropperBottleinWarmStudioLight.png": "front view"},
        },
        "rosart-rose-face-mask-80ml": {
            "seo.title": "Rose Face Mask 80ml, Hydrating Facial Mask | Rosart",
            "alt": {
                "RosartBulgarianRoseFaceMaskDuo.png": "with its box and spoon",
                "RosartRoseMaskGardenStillLife.png": "with a rose",
                "RosartRoseMaskinBloom.png": "held by a model among roses",
            },
            "category": MASKS,
        },
        "rose-cleansing-balm-80ml": {
            "seo.title": "Rose Cleansing Balm 80ml, Makeup Remover | Rosart",
            "seo.description": (
                "Rosart Rose Cleansing Balm 80ml with rosehip, jojoba and avocado oils gently removes makeup "
                "and impurities, leaving skin soft. Made in Bulgaria."
            ),
            "alt": {
                "RosartRoseCleansingBalmDisplay.png": "with its box",
                "RosartRoseBalminaSunlitGarden.png": "with its box among roses",
                "SunlitRoseCleansingBalmPortrait.png": "held by a model in a rose field",
            },
            "category": CLEANSERS,
        },
        "rose-face-cream-3g": {
            "seo.title": "Rose Face Cream 3g, Nourishing Moisturizer | Rosart",
            "alt": {
                "BulgarianRoseCream3g_14a773ff-e665-4af0-8662-6debcfef1347.jpg": "with its box",
                "BulgarianRoseFaceCreamStillLife_ca18a3dc-fd34-4fcd-90fa-796fa6294fe9.png": "with its box and rose petals",
            },
        },
        "rose-face-cream-50ml": {
            "seo.title": "Rose Face Cream 50ml, Nourishing Moisturizer | Rosart",
            "alt": {
                "BulgarianRoseCreamDuo.jpg": "with its box and spoon",
                "ESPRITHONGKONG_1.png": "with its box among roses in a garden",
                "fc1.png": "with a rose and petals",
                "SunlitRoseFaceCreamPortrait.png": "held by a model in a rose field",
            },
        },
        "rose-face-souffle-cream-3g": {
            "seo.title": "Rose Face Soufflé Cream 3g, Lightweight Moisturiser | Rosart",
            "alt": {"RosartRoseSouffleCreamSet.png": "with its box"},
        },
        "rose-face-souffle-cream-50ml": {
            "seo.title": "Rose Face Soufflé Cream 50ml, Light Moisturizer | Rosart",
            "seo.description": (
                "Rosart Rose Face Soufflé Cream 50ml: a lightweight facial moisturizer with Damascena rose "
                "flower water, rose oil and rosehip oil. Made in Bulgaria."
            ),
            "alt": {
                "Curved-LabelRosartRoseCreamSet.png": "with its box and spoon",
                "RosartRoseSouffleStillLife.png": "with its box among roses",
            },
        },
        "rose-facial-toner-330ml": {
            "seo.title": "Rose Facial Toner 330ml with Damask Rose Water | Rosart",
            "alt": {
                "RST_65e67bcf-5d90-4538-8ea6-8383481d6367.png": "with its box",
                "RosartRoseTonerStillLife.png": "with a rose",
                "1_e8d51abc-fa25-4a53-8000-2a109dc374e9.png": "held in a hand among roses",
                "2_3f29349c-2ca4-4df1-a6a4-11f29261cce9.png": "held up by a model in a rose field",
            },
            "category": TONERS,
        },
        "rose-glycerin-soap-90g": {
            "seo.title": "Rose Glycerin Soap 90g, Handmade with Rose Petals | Rosart",
            "alt": {
                "RosartRoseGlycerinSoapSet.png": "with its box",
                "Rose-GoldGlycerinSoapPackaging.png": "with its box and roses",
                "RosartRoseSoapinSunlitGarden_3f7fc8b5-0d9f-477b-88d5-ba98bfe3eaa7.png": "with its box in a sunlit garden",
                "SunlitRoseGardenwithAmberDisc.png": "held up by a model in a rose field",
            },
            "category": BAR_SOAP,
        },
        "rose-oil-revitalizing-essence-30-capsules": {
            "seo.title": "Rose Oil Revitalizing Essence, 30 Capsules | Rosart",
            "alt": {
                "GlossyGoldenSoftgelCapsules.png": "with its box and loose capsules",
                "RST_1_c9e99047-71cb-42e8-86ac-bfdbad6c3da8.png": "capsules in an open container among roses",
                "RST_a7c30e84-49fb-48b6-a53d-de7ec63c08d5.png": "open container with a capsule on a spoon",
                "RosartGoldSkincareJarinBloomingGarden.png": "container held in a hand among roses",
                "SunlitRoseGardenSkincarePortrait.png": "held by a model in a rose field",
                "GoldenCapsuleCuttingGuide.png": (
                    "how to use: cut open the capsule with the mini scissors, squeeze out the essence, "
                    "massage onto face and neck"
                ),
            },
            "category": SERUMS,
        },
    },
    "image_observations": {
        "RosartBulgarianRoseWaterSet.png": "product beside its box; text: Rosart Rose Water, 10 ML",
        "ESPRITHONGKONG_3.png": "small spray container on a tray among roses in a garden; label: Rosart Rose Water",
        "BulgarianRoseRosartWaterSet.png": "product beside its box; text: Rosart Rose Water, 100 ML",
        "RST.png": "bottle with roses; label: Rosart Rose Water",
        "RST_2.png": "bottle on a tray among roses in a garden",
        "RST_1.png": "bottle held in a hand among roses (label partly garbled)",
        "RosartRoseWaterinaSunlitRoseGarden.png": "model holding a bottle in a rose field; label unreadable",
        "RosartBulgarianRoseEssenceMaskBox.png": "front of the box; text: Rosart Bulgarian Rose Hydrosol Essence Mask, 5 pcs x 25ml",
        "RosartBulgarianRoseMaskShowcase.png": "box and a single sachet among roses",
        "RST_007bac73-3152-441e-9d95-86ccd6e85270.png": "model holding a sachet in a rose field; text too small",
        "RST2.png": "two sachets with roses; sachet text 'Bulgarian Rose Water Essence Mask', partly garbled",
        "Rosart_Rosehip_4x5_2.png": "bottle beside its box; text: Rosart Pure Face Rosehip Oil, 20 ML",
        "RosartRosehipOilinaSunlitGarden.png": "bottle and box among roses in a garden",
        "Rosart_Rosehip_4x5_3.png": "bottle with roses and a ribbon; label: Rosart Pure Face Rosehip Oil 20ML",
        "Rosart_Rosehip_4x5_1.png": "bottle, front view, plain backdrop; label: Rosart Pure Face Rosehip Oil 20ML",
        "RosartSmallDropperBottleinWarmStudioLight.png": "small dropper, front view; only the Rosart logo is visible",
        "RosartBulgarianRoseFaceMaskDuo.png": "jar beside its box with a spoon; text: Rosart Rose Face Mask, 80 ML",
        "RosartRoseMaskGardenStillLife.png": "jar on a tray with a rose; label: Rosart Rose Face Mask",
        "RosartRoseMaskinBloom.png": "model holding the jar among roses",
        "RosartRoseCleansingBalmDisplay.png": "jar beside its box; text: Rosart Rose Cleansing Balm, 80 ML",
        "RosartRoseBalminaSunlitGarden.png": "jar and box among roses",
        "SunlitRoseCleansingBalmPortrait.png": "model holding the jar in a rose field; label: Rosart Rose Cleansing Balm",
        "BulgarianRoseCream3g_14a773ff-e665-4af0-8662-6debcfef1347.jpg": "jar beside its box; text: Rosart Rose Face Cream, 3g",
        "BulgarianRoseFaceCreamStillLife_ca18a3dc-fd34-4fcd-90fa-796fa6294fe9.png": "jar and box with rose petals; 3g",
        "BulgarianRoseCreamDuo.jpg": "jar beside its box with a spoon; text: Rosart Rose Face Cream, 50 ML",
        "ESPRITHONGKONG_1.png": "jar and box among roses in a garden; 50 ML",
        "fc1.png": "jar with a rose and petals; label: Rosart Rose Face Cream (slightly garbled)",
        "SunlitRoseFaceCreamPortrait.png": "model holding a jar in a rose field; label barely legible",
        "RosartRoseSouffleCreamSet.png": "jar beside its box; text: Rosart Rose Face Soufflé Cream, 3g",
        "Curved-LabelRosartRoseCreamSet.png": "jar beside its box with a spoon; text: Rose Face Soufflé Cream, 50 ML",
        "RosartRoseSouffleStillLife.png": "jar and box among roses; label: Rosart Rose Face Soufflé Cream",
        "RST_65e67bcf-5d90-4538-8ea6-8383481d6367.png": "bottle beside its box; text: Rosart Rose Facial Toner, 330 ML",
        "RosartRoseTonerStillLife.png": "bottle with a rose; label: Rosart Rose Facial Toner",
        "1_e8d51abc-fa25-4a53-8000-2a109dc374e9.png": "bottle held in a hand among roses; label: Rosart Rose Facial Toner",
        "2_3f29349c-2ca4-4df1-a6a4-11f29261cce9.png": "model holding up the bottle in a rose field",
        "RosartRoseGlycerinSoapSet.png": "soap beside its box; text: Rosart Glycerin Soap",
        "Rose-GoldGlycerinSoapPackaging.png": "soap and box with roses",
        "RosartRoseSoapinSunlitGarden_3f7fc8b5-0d9f-477b-88d5-ba98bfe3eaa7.png": "soap and box in a sunlit garden",
        "SunlitRoseGardenwithAmberDisc.png": "model holding up the soap bar in a rose field",
        "GlossyGoldenSoftgelCapsules.png": "jar beside its box with loose capsules; text: Rosart Rose Oil Revitalizing Essence, 30 Capsules",
        "RST_1_c9e99047-71cb-42e8-86ac-bfdbad6c3da8.png": "open container of capsules among roses (the box shown is labelled Rose Face Cream)",
        "RST_a7c30e84-49fb-48b6-a53d-de7ec63c08d5.png": "open container with a capsule on a spoon",
        "RosartGoldSkincareJarinBloomingGarden.png": "container held in a hand among roses",
        "SunlitRoseGardenSkincarePortrait.png": "model holding a small container in a rose field",
        "GoldenCapsuleCuttingGuide.png": (
            "infographic 'How to Use': Cut open the capsule with the mini scissors; gently squeeze out the "
            "essence; massage evenly onto cleansed face and neck"
        ),
    },
    "files": {
        "ESPRIT_HONG_KONG.png": {"alt": "Rosart", "use": "site logo, header and footer",
                                 "observed": "Rosart script wordmark"},
        "Add_a_heading_fcec8aa5-9bda-44ce-96bc-7966b68e00f2.png": {
            "alt": "Our Story", "use": "Our Story page banner", "observed": "the words 'Our Story' over rose petals"},
        "USDA_and_EU_Organic_Certifications.png": {
            "alt": "USDA and EU Organic Certification: Ceres certified, EU organic, USDA Organic and GMP certified",
            "use": "homepage certifications image",
            "observed": ("heading 'USDA and EU Organic Certification'; marks: CERES CERTIFIED, EU organic leaf, "
                         "USDA ORGANIC, GMP CERTIFIED")},
        "Rosart_Distillery_in_Bulgaria_s_Valley_of_Roses.png": {
            "alt": ("Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the "
                    "heart of Bulgaria's Valley of Roses"),
            "use": "Our Story map banner (desktop)",
            "observed": ("map titled 'Rosart | Our Roots in Bulgaria': 'Our organic rose fields and distillery in "
                         "Zlatosel, in the heart of Bulgaria's Valley of Roses.'; labels BULGARIA, Valley of Roses, "
                         "Zlatosel, Rosart Distillery")},
        "Rosart_s_Bulgarian_Valley_of_Roses_Map.png": {
            "alt": ("Map, Our Roots in Bulgaria: Rosart's organic rose fields and distillery in Zlatosel, in the "
                    "heart of Bulgaria's Valley of Roses"),
            "use": "Our Story map banner (mobile)",
            "observed": ("map titled 'Our Roots in Bulgaria' with the Rosart logo: 'Our organic rose fields and "
                         "distillery in Zlatosel, in the heart of Bulgaria's Valley of Roses.'")},
        "ARTEC.png": {"alt": "Artec Chemical Group", "use": "Our Story 'Our Global Connections', parent company",
                      "observed": "logo: Artec Chemical Company Limited, ARTEC CHEMICAL GROUP; theme img alt 'Artec Chemical Group'"},
        "ROSART.png": {"alt": "Rosart Bulgaria", "use": "Our Story 'Our Global Connections', brand origin",
                       "observed": "Rosart script wordmark; theme img alt 'Rosart Bulgaria'"},
        "ORIZEN.png": {"alt": "ORIZEN Australia", "use": "Our Story 'Our Global Connections', Australian retail partner",
                       "observed": "ORIZEN monogram and wordmark; theme img alt 'ORIZEN Australia'"},
        "Rosart_Rose_Balm_in_a_Sunlit_Garden.png": {
            "alt": "Rosart Rose Cleansing Balm with its box among roses",
            "use": "Our Story image beside 'Nature, Refined for Your Skin'",
            "observed": "jar and box among roses; box text: Rosart, Rose Cleansing Balm, 80 ML"},
        "ESPRIT_HONG_KONG_f0a060ae-af0e-4c30-b417-84e0f08f40c7.png": {
            "alt": "Pickers harvesting roses in a rose field",
            "use": "homepage image beside 'Our Roses. Our Fields. Our Craft.'",
            "observed": "aerial view of pickers harvesting roses into baskets in a rose field"},
    },
    "decorative": {
        "1.png": "homepage collection tile; the caption 'Face Care' beside it names the link (site renders alt=\"\")",
        "2.png": "homepage collection tile; the caption 'Rose Water & Toners' names the link (site renders alt=\"\")",
        "3.png": "homepage collection tile; the caption 'Masks & Treatments' names the link (site renders alt=\"\")",
        "bg1.png": "section background (blurred roses)",
        "bg3.png": "section background (rose petal texture)",
        "Untitled_design_7_f6c26f2b-ad40-440e-a57a-fb6da4fe0dcb.png": "homepage hero (desktop); the overlaid heading 'From Soil to Skin' carries the message",
        "DSC08666_Rosart_Mobile_1200x1500_e230edb1-8068-47e5-b216-efa8170e4f7e.jpg": "homepage hero (mobile), mood photo under the same heading",
        "Untitled_design_8_7171c05c-c755-42c7-b662-fc397c08f2bb.png": "Our Story landscape banner (rose field), no information",
        "DSC06467.jpg": "Our Story mood photo (roses in a scale pan) beside the distillation text",
    },
    "findings": [
        {"topic": "Hydrosol Essence Mask carries the toner's details",
         "finding": ("The four custom metafields of `bulgarian-rose-hydrosol-essence-mask-5pcs` (how_to_use, "
                     "the_ingredients, certifications_brand_values, caution) are the Rose Facial Toner's text: "
                     "'Pour an appropriate amount onto a cotton pad...', '...the toner's botanical formula'. The "
                     "site shows these on the mask page. This plan fixes only the mask's SEO (also the toner's) "
                     "and does not touch verbatim metafield text."),
         "evidence": "export/shopify/products.json, product gid://shopify/Product/16432873701457",
         "decision": "the owner supplies the mask's own how-to-use, ingredients (INCI) and caution from its packaging"},
        {"topic": "Another business named as the site operator",
         "finding": ("'MinervaBelle & Co.' is named on the Contact page and as the operator in the Privacy policy and "
                     "Terms of service; the Contact page and Privacy policy give info@deniva.com.au (Deniva), not "
                     "info@rosart.com.au. The site's dist check rejects any email other than the enquiry email."),
         "evidence": "pages.json contact bodyHtml; shopPolicies PRIVACY_POLICY and TERMS_OF_SERVICE (read 2026-11-07)",
         "decision": "which business name and email Rosart's Australian site should show"},
        {"topic": "Policies",
         "finding": ("Privacy policy and Terms of service exist; refund, shipping and contact-information policies do "
                     "not (the site sells nothing, so refund/shipping may be N/A). The Privacy policy says the website "
                     "is 'hosted on Shopify' and mentions a contact form; the static site is on S3/CloudFront and has "
                     "no form. The static site's URL map has no /policies/ pages, so neither policy is published."),
         "decision": "policy set to publish on the static site, and updated hosting wording"},
        {"topic": "Product category is 'Face Moisturizers' on all 14 products",
         "finding": ("Planned: toner and both rose waters to Toners, both masks to Skin Care Masks & Peels, balm to "
                     "Facial Cleansers, soap to Bar Soap, capsules to Face Serums (8 changes). Creams stay Face "
                     "Moisturizers. Rosehip oil (type 'Facial Oil') is left as Face Moisturizers: the taxonomy has no "
                     "face-oil leaf; Face Serums is the alternative."),
         "decision": "rosehip oil category (keep Face Moisturizers or move to Face Serums)"},
        {"topic": "Homepage SEO does not reach the static site",
         "finding": ("Shop.description is empty and the homepage title is unset. The static site does not read either: "
                     "src/pages/index.astro builds the title as 'Rosart | <footer text>' and the description from the "
                     "homepage sections, and the export does not capture Shop.description. After the owner sets them "
                     "in Online Store > Preferences, the exporter and index page need a change to use them "
                     "(other agent's files; not edited here)."),
         "decision": "whether the site should take its homepage title/description from Shopify"},
        {"topic": "Hard-coded image alt in the site",
         "finding": ("src/site.config.ts `imageAlt` holds 5 alt texts. This plan writes them (plus 5 more) to the Shopify "
                     "file alt, which the export already reads (files.json alt). After apply and re-export, "
                     "`imageAlt` can be emptied (other agent's file; not edited here)."),
         "decision": None},
        {"topic": "Theme social links are placeholders",
         "finding": ("Footer social links point to bare https://www.facebook.com/, https://www.instagram.com/, "
                     "https://www.tiktok.com/, https://x.com/, https://www.youtube.com/."),
         "evidence": "theme-content.json footer-group social-links block",
         "decision": "real profile URLs, or remove the block"},
        {"topic": "Homepage hero button goes to the wrong place",
         "finding": "The hero button 'Discover Our Story' links to shopify://collections/all (the catalogue), not the Our Story page.",
         "evidence": "theme-content.json templates/index.json section 0, block 2",
         "decision": "link to /pages/our-story or relabel"},
        {"topic": "Collection handles do not match titles",
         "finding": ("'Rose Water & Toners' has handle face-care-copy and 'Masks & Treatments' has handle "
                     "rose-water-toners-copy. The static site slugs collections by title, so URLs are fine; handles "
                     "are not changed by this plan."),
         "decision": None},
        {"topic": "Product images that may mislead",
         "finding": ("Rose Oil Revitalizing Essence image RST_1_c9e99047-...png shows a box labelled 'Rose Face Cream 50 ML "
                     "... with rose essential oil & vitamin E capsules'. Mask image RST2.png and theme tile 3.png show "
                     "sachets reading 'Bulgarian Rose Water Essence Mask' with garbled text (look AI-generated); fc1.png "
                     "and RST_1.png labels are also garbled. The 2ml rosehip oil's only image shows no size or product "
                     "name. Several theme files are named ESPRIT_HONG_KONG*.png (leftover filenames; content is Rosart)."),
         "decision": "replace or keep these images"},
        {"topic": "Metafield formatting",
         "finding": ("Literal markdown asterisks in multi-line metafields: '**Full Ingredients (INCI)**' (Rose Cleansing "
                     "Balm the_ingredients) and '**Organic Rose Oil**' etc. (Rose Face Cream 3g the_ingredients). Rose "
                     "Cleansing Balm certifications_brand_values repeats 'No artificial fragrance added.' twice. "
                     "Verbatim source text, not changed here."),
         "decision": "clean up in the admin"},
        {"topic": "Certification marks",
         "finding": ("The homepage certification image shows CERES, EU organic, USDA Organic and GMP marks; product "
                     "metafields say certification scope 'is subject to the manufacturer's documentation'."),
         "decision": "confirm each mark is held for the products shown (claims with legal weight)"},
        {"topic": "Store basics",
         "finding": ("Shop primary domain is still rosart-mhfmyuxq.myshopify.com (fine while the static site is the "
                     "public face); store contact email is share@solagratia.com.au; the 'Your Privacy Choices' "
                     "(data-sharing-opt-out) page and footer 'Search' item are Shopify defaults the site drops. "
                     "Related links on Our Story (artechem.com, rosart-bg.com, orizen.com.au) are the parent company, "
                     "brand origin and retail partner, not stray businesses."),
         "decision": None},
        {"topic": "Tags",
         "finding": "Every product carries the tag 'Face Care', including rose waters, oils, masks and capsules.",
         "decision": "optional tidy-up (tags do not drive collections here: collections are manual)"},
    ],
    "references": [
        "Product SEO: `productUpdate` with `ProductUpdateInput.seo` (`SEOInput {title, description}`); stored as "
        "`global.title_tag` / `global.description_tag`. Requires `write_products`. "
        "https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/ProductUpdateInput, "
        "https://shopify.dev/docs/apps/build/marketing/optimize-storefront-seo",
        "Product category: `ProductUpdateInput.category: ID` (TaxonomyCategory gid); ids from `taxonomy { categories(search:) }`. "
        "https://shopify.dev/docs/api/admin-graphql/2026-10/queries/taxonomy",
        "Image alt: `fileUpdate(files: [{id, alt}])` on the MediaImage gid; requires `write_files` (or `write_themes`). "
        "`productUpdateMedia` is deprecated. Alt lives on the file, so read it back after writing "
        "(checklist section 3). https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/fileUpdate",
        "Collections: `collectionUpdate(collection: CollectionUpdateInput)` with `descriptionHtml`, `seo` and "
        "`image: ImageInput {id, altText}`; requires `write_products`. "
        "https://shopify.dev/docs/api/admin-graphql/2026-10/mutations/collectionUpdate",
        "Pages: `PageUpdateInput` has no `seo` field; SEO is the `global.title_tag` / `global.description_tag` metafields "
        "via `pageUpdate(id, page: {metafields})`; requires `write_content` or `write_online_store_pages`. "
        "https://shopify.dev/docs/api/admin-graphql/2026-10/input-objects/PageUpdateInput",
        "Homepage: `Shop.description` is 'the shop's meta description used in search engine results' and is read-only; "
        "no Admin API mutation sets it or the homepage title, so both are set in Online Store > Preferences. "
        "https://shopify.dev/docs/api/admin-graphql/2026-10/objects/Shop",
    ],
}

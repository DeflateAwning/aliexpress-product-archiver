use scraper::{Html, Selector};
use anyhow::Result;
use crate::models::Product;

pub fn parse_product(product_id: u64, html: &str) -> Result<Product> {
    let doc = Html::parse_document(html);
    let title_selector = Selector::parse("h1.product-title-text").unwrap();

    let title = doc
        .select(&title_selector)
        .next()
        .map(|e| e.inner_html().trim().to_string())
        .unwrap_or_else(|| "Unknown Title".into());

    Ok(Product {
        product_id: product_id,
        title,
        timestamp: chrono::Utc::now().to_rfc3339(),
    })
}

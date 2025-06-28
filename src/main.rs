mod fetcher;
mod parser;
mod models;
mod archiver;

use anyhow::Result;


fn main() -> Result<()> {
    let product_id: u64 = 1005007181903595;
    let url = get_product_url(product_id);
    let html = fetcher::fetch_html(&url)?;

    // Debugging: Write the HTML to a file
    std::fs::write(format!("product_page_{}.html", product_id), &html)
        .expect("Failed to write HTML to file");

    let product = parser::parse_product(product_id, &html)?;

    archiver::save_to_file(&product, "archive.json")?;
    println!("Product archived successfully.");
    Ok(())
}

fn get_product_url(product_id: u64) -> String {
    format!("https://vi.aliexpress.com/item/{}.html", product_id).to_string()
}

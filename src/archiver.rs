use std::fs::File;
use std::io::Write;
use anyhow::Result;
use crate::models::Product;

pub fn save_to_file(product: &Product, filename: &str) -> Result<()> {
    let json = serde_json::to_string_pretty(product)?;
    let mut file = File::create(filename)?;
    file.write_all(json.as_bytes())?;
    Ok(())
}

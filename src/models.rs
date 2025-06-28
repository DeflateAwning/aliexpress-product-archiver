use serde::{Serialize, Deserialize};

#[derive(Serialize, Deserialize)]
pub struct Product {
    pub product_id: u64,
    pub title: String,
    pub timestamp: String,
}

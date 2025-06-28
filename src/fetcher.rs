use anyhow::Result;
use reqwest::blocking::get;
use reqwest::redirect;

pub fn _fetch_html_old(url: &str) -> Result<String> {
    let resp = get(url)?;
    Ok(resp.text()?)
}

pub fn fetch_html(url: &str) -> Result<String, reqwest::Error> {
    let custom_redirect_policy = redirect::Policy::custom(|attempt| {
        if attempt.previous().len() > 100 {
            attempt.error("Too many redirects (>100)")
        } else if attempt.url().host_str() == Some("example.domain") {
            // prevent redirects to 'example.domain'
            attempt.stop()
        } else {
            attempt.follow()
        }
    });

    let client = reqwest::blocking::Client::builder()
        .redirect(custom_redirect_policy)
        .build()?;

    client.get(url)
        .header("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3")
        .send()?
        .text()
        .map_err(|e| e.into())
}

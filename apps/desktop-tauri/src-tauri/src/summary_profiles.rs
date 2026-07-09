use anyhow::{bail, Context, Result};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine as _;
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct SummaryProfile {
    pub name: String,
    pub base_url: String,
    pub model: String,
    #[serde(default)]
    pub api_key: String,
}

#[derive(Debug, Clone, Default, Serialize)]
pub struct SummaryProfilesState {
    pub profiles: Vec<SummaryProfile>,
    pub last_profile: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct StoredSummaryProfile {
    pub name: String,
    #[serde(default)]
    pub base_url: String,
    #[serde(default)]
    pub model: String,
    #[serde(default)]
    pub encrypted_api_key: String,
}

#[derive(Debug, Default, Serialize, Deserialize)]
struct StoredSummaryProfilesFile {
    #[serde(default)]
    pub last_profile: Option<String>,
    #[serde(default)]
    pub profiles: Vec<StoredSummaryProfile>,
}

pub fn load_profiles(project_root: &Path) -> Result<SummaryProfilesState> {
    let path = profiles_path(project_root);
    if !path.exists() {
        return Ok(SummaryProfilesState::default());
    }

    let content = std::fs::read_to_string(&path)
        .with_context(|| format!("failed to read {}", path.display()))?;
    let stored: StoredSummaryProfilesFile =
        toml::from_str(&content).with_context(|| format!("failed to parse {}", path.display()))?;

    let profiles = stored
        .profiles
        .into_iter()
        .map(|profile| {
            Ok(SummaryProfile {
                name: profile.name,
                base_url: profile.base_url,
                model: profile.model,
                api_key: decrypt_secret(&profile.encrypted_api_key)?,
            })
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(SummaryProfilesState {
        profiles,
        last_profile: stored.last_profile.filter(|value| !value.trim().is_empty()),
    })
}

pub fn upsert_profile(
    project_root: &Path,
    profile: &SummaryProfile,
) -> Result<SummaryProfilesState> {
    let mut state = load_profiles(project_root)?;
    let normalized_name = profile.name.trim();
    if normalized_name.is_empty() {
        bail!("profile name is empty");
    }

    let normalized = SummaryProfile {
        name: normalized_name.to_string(),
        base_url: profile.base_url.trim().to_string(),
        model: profile.model.trim().to_string(),
        api_key: profile.api_key.trim().to_string(),
    };

    if let Some(existing) = state
        .profiles
        .iter_mut()
        .find(|item| item.name.eq_ignore_ascii_case(normalized_name))
    {
        *existing = normalized.clone();
    } else {
        state.profiles.push(normalized.clone());
    }

    state
        .profiles
        .sort_by(|left, right| left.name.to_lowercase().cmp(&right.name.to_lowercase()));
    state.last_profile = Some(normalized.name);
    save_profiles(project_root, &state)?;
    Ok(state)
}

pub fn delete_profile(project_root: &Path, name: &str) -> Result<SummaryProfilesState> {
    let normalized_name = name.trim();
    let mut state = load_profiles(project_root)?;
    state
        .profiles
        .retain(|profile| !profile.name.eq_ignore_ascii_case(normalized_name));

    if state
        .last_profile
        .as_deref()
        .is_some_and(|value| value.eq_ignore_ascii_case(normalized_name))
    {
        state.last_profile = state.profiles.first().map(|profile| profile.name.clone());
    }

    save_profiles(project_root, &state)?;
    Ok(state)
}

pub fn profiles_path(project_root: &Path) -> PathBuf {
    project_root.join("config").join("summary_profiles.toml")
}

fn save_profiles(project_root: &Path, state: &SummaryProfilesState) -> Result<()> {
    let path = profiles_path(project_root);
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .with_context(|| format!("failed to create {}", parent.display()))?;
    }

    let stored = StoredSummaryProfilesFile {
        last_profile: state.last_profile.clone(),
        profiles: state
            .profiles
            .iter()
            .map(|profile| {
                Ok(StoredSummaryProfile {
                    name: profile.name.clone(),
                    base_url: profile.base_url.clone(),
                    model: profile.model.clone(),
                    encrypted_api_key: encrypt_secret(&profile.api_key)?,
                })
            })
            .collect::<Result<Vec<_>>>()?,
    };

    let content =
        toml::to_string_pretty(&stored).context("failed to serialize summary profiles")?;
    std::fs::write(&path, content).with_context(|| format!("failed to write {}", path.display()))?;
    Ok(())
}

#[cfg(windows)]
fn encrypt_secret(value: &str) -> Result<String> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    if value.trim().is_empty() {
        return Ok(String::new());
    }

    let mut input_bytes = value.as_bytes().to_vec();
    let input = CRYPT_INTEGER_BLOB {
        cbData: input_bytes.len() as u32,
        pbData: input_bytes.as_mut_ptr(),
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };

    let status = unsafe {
        CryptProtectData(
            &input,
            null(),
            null(),
            null(),
            null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if status == 0 {
        bail!("CryptProtectData failed: {}", std::io::Error::last_os_error());
    }

    let encrypted = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let encoded = BASE64_STANDARD.encode(encrypted);
    unsafe {
        LocalFree(output.pbData.cast());
    }
    Ok(encoded)
}

#[cfg(windows)]
fn decrypt_secret(value: &str) -> Result<String> {
    use std::ptr::{null, null_mut};
    use windows_sys::Win32::Foundation::LocalFree;
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    if value.trim().is_empty() {
        return Ok(String::new());
    }

    let mut encrypted =
        BASE64_STANDARD.decode(value.trim()).context("failed to decode encrypted API key")?;
    let input = CRYPT_INTEGER_BLOB {
        cbData: encrypted.len() as u32,
        pbData: encrypted.as_mut_ptr(),
    };
    let mut description = null_mut();
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: null_mut(),
    };

    let status = unsafe {
        CryptUnprotectData(
            &input,
            &mut description,
            null(),
            null(),
            null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        )
    };
    if status == 0 {
        bail!("CryptUnprotectData failed: {}", std::io::Error::last_os_error());
    }

    let decrypted = unsafe { std::slice::from_raw_parts(output.pbData, output.cbData as usize) };
    let text =
        String::from_utf8(decrypted.to_vec()).context("decrypted API key is not valid UTF-8")?;
    unsafe {
        if !description.is_null() {
            LocalFree(description.cast());
        }
        LocalFree(output.pbData.cast());
    }
    Ok(text)
}

#[cfg(not(windows))]
fn encrypt_secret(value: &str) -> Result<String> {
    Ok(value.to_string())
}

#[cfg(not(windows))]
fn decrypt_secret(value: &str) -> Result<String> {
    Ok(value.to_string())
}

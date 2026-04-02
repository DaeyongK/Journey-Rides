function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var payload = JSON.parse(e.postData.contents);
    
    var action = payload.action || "add"; 
    var aid = payload.announcement_id;
    var content = payload.content_category;
    var sheetName = "";
    
    // Target the correct sheet based on category
    if (content === "F") { 
      sheetName = "Friday PM Imports";
    } else if (content === "S") {
      sheetName = "Sunday Service Imports";
    } else {
      return ContentService.createTextOutput("Error: Invalid content category.");
    }
   
    var sheet = ss.getSheetByName(sheetName);
    if (!sheet) return ContentService.createTextOutput("Error: No sheet found named " + sheetName);

    // ==========================================
    // AUTO-RESET & MEMORY LOGIC 
    // ==========================================
    var props = PropertiesService.getDocumentProperties();
    var memoryKey = "active_aid_" + content; 
    var savedAid = props.getProperty(memoryKey);

    // TRIGGERED BY PYTHON ON SEND_AT: Wipe sheet and memorize new ID
    if (action === "reset") {
      sheet.clear(); 
      props.setProperty(memoryKey, aid); 
      return ContentService.createTextOutput("✅ Sheet wiped and tracking new ID: " + aid);
    }

    // If someone clicks a button on an old announcement, ignore it.
    if (savedAid && savedAid !== aid) {
      return ContentService.createTextOutput("⚠️ Ignored: This announcement is no longer active.");
    }

    // Calculates column placements for data
    var rowNum = parseInt(payload.count);
    if (rowNum < 1) rowNum = 1; 

    var school = payload.school; 
    var role = payload.role.toLowerCase(); 
    var schools = ["GT", "Emory", "GSU"];
    
    var schoolIndex = schools.indexOf(school);
    if (schoolIndex === -1) schoolIndex = 0; // Fallback to GT if something goes weird
    
    var startIndex = schoolIndex * 8; 

    // Withdrawal logic
    if (action === "delete") {
      if (role === "driver") {
        sheet.getRange(rowNum, startIndex + 1, 1, 4).clearContent();
      } else {
        sheet.getRange(rowNum, startIndex + 5, 1, 3).clearContent();
      }
      return ContentService.createTextOutput("✅ Cleared row " + rowNum);
    }
    
    // Sign up logic
    if (action === "add") {
      if (role === "driver") {
        sheet.getRange(rowNum, startIndex + 1, 1, 4).setValues([[
          payload.name, 
          payload.seats, 
          payload.phone, 
          payload.info
        ]]);
      } else {
        sheet.getRange(rowNum, startIndex + 5, 1, 3).setValues([[
          payload.name, 
          payload.phone, 
          payload.info
        ]]);
      }
      return ContentService.createTextOutput("✅ Success adding to row " + rowNum);
    }
    
  } catch (error) {
    return ContentService.createTextOutput("Apps Script Error: " + error.message);
  }
}
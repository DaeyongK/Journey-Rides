function doPost(e) {
  try {
    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var payload = JSON.parse(e.postData.contents);
    
    var action = payload.action || "add"; 
    var aid = payload.announcement_id;
    var content = payload.content_category;
    var sheetName = "";
    
    // 1. Target the correct sheet based on category
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

    // If memory is blank (first time running), just trust this ID
    if (!savedAid && action !== "reset") {
      props.setProperty(memoryKey, aid);
      savedAid = aid;
    }

    if (savedAid !== aid && action !== "reset") {
      return ContentService.createTextOutput("⚠️ Ignored: This announcement is no longer active.");
    }

    var rowNum = parseInt(payload.count);
    if (rowNum < 1) rowNum = 1; 

    // ==========================================
    // DELETION LOGIC 
    // ==========================================
    if (action === "delete") {
      var school = payload.school; 
      var role = payload.role.toLowerCase(); 
      var schools = ["GT", "Emory", "GSU"];
      
      var schoolIndex = schools.indexOf(school);
      if (schoolIndex === -1) schoolIndex = 0; 
      var startIndex = schoolIndex * 8; 
      
      if (role === "driver") {
        sheet.getRange(rowNum, startIndex + 1, 1, 4).clearContent();
        sheet.getRange(rowNum, startIndex + 8).clearDataValidations().clearContent(); // Clears checkbox
      } else {
        sheet.getRange(rowNum, startIndex + 5, 1, 3).clearContent();
        sheet.getRange(rowNum, startIndex + 8).clearDataValidations().clearContent(); // Clears checkbox
      }
      
      return ContentService.createTextOutput("✅ Cleared row " + rowNum);
    }
    
    // ==========================================
    // ADDITION LOGIC 
    // ==========================================
    if (action === "add") {
      var school = payload.school; 
      var role = payload.role.toLowerCase(); 
      var schools = ["GT", "Emory", "GSU"]; 
      
      var schoolIndex = schools.indexOf(school);
      if (schoolIndex === -1) schoolIndex = 0; 
      var startIndex = schoolIndex * 8; 
      
      if (role === "driver") {
        sheet.getRange(rowNum, startIndex + 1).setValue(payload.name);
        sheet.getRange(rowNum, startIndex + 2).setValue(payload.seats);
        sheet.getRange(rowNum, startIndex + 3).setValue(payload.phone);
        sheet.getRange(rowNum, startIndex + 4).setValue(payload.info);
      } else {
        sheet.getRange(rowNum, startIndex + 5).setValue(payload.name);
        sheet.getRange(rowNum, startIndex + 6).setValue(payload.phone);
        sheet.getRange(rowNum, startIndex + 7).setValue(payload.info);
      }
      
      var checkboxCell = sheet.getRange(rowNum, startIndex + 8);
      checkboxCell.setValue(false);
      checkboxCell.insertCheckboxes();
      
      return ContentService.createTextOutput("✅ Success adding to row " + rowNum + " | Google received count: " + payload.count);
    }
    
  } catch (error) {
    return ContentService.createTextOutput("Apps Script Error: " + error.message);
  }
}